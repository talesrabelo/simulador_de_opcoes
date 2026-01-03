import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings
import plotly.graph_objects as go

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Backtest Opções vs CDI",
    page_icon="📈",
    layout="wide"
)

warnings.simplefilter(action='ignore', category=FutureWarning)

# --- ESTILO CSS ---
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.05);
    }
    .metric-label {
        font-size: 14px;
        color: #6c757d;
        text-transform: uppercase;
        font-weight: 600;
    }
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        margin-top: 5px;
    }
    .positive { color: #28a745; }
    .negative { color: #dc3545; }
    .warning { color: #ffc107; }
    .info { color: #17a2b8; }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES ---

@st.cache_data(ttl=86400)
def pegar_cdi_bcb():
    """Baixa histórico do CDI do Banco Central"""
    try:
        url = 'http://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=csv'
        df = pd.read_csv(url, sep=';')
        df['data'] = pd.to_datetime(df['data'], format='%d/%m/%Y')
        df['valor'] = df['valor'].str.replace(',', '.').astype(float)
        df['fator'] = 1 + (df['valor'] / 100)
        df.set_index('data', inplace=True)
        return df[['fator']]
    except:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def baixar_dados(ticker, inicio, fim):
    try:
        fim_ajustado = fim + timedelta(days=200)
        df = yf.download(ticker, start=inicio, end=fim_ajustado, progress=False, auto_adjust=False)
        if df.empty: return pd.DataFrame()
        
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df.columns = df.columns.get_level_values('Price')
            except:
                df.columns = df.columns.get_level_values(0)
        
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df
    except:
        return pd.DataFrame()

def calcular_leg(pr_ent, pr_sai, qtde, tipo, posicao, offset_pct, premio_pct):
    strike = pr_ent * (1 + offset_pct/100.0)
    premio_un = pr_ent * (premio_pct/100.0)
    fin_premio = premio_un * qtde
    
    payoff_un = 0.0
    exercido = False
    
    if tipo == 'Call':
        payoff_un = max(0, pr_sai - strike)
        if pr_sai > strike: exercido = True
    elif tipo == 'Put':
        payoff_un = max(0, strike - pr_sai)
        if pr_sai < strike: exercido = True
        
    fin_payoff = payoff_un * qtde
    taxa_ent = fin_premio * 0.005
    taxa_sai = (strike * qtde * 0.005) if exercido else 0.0
    custos = taxa_ent + taxa_sai
    
    res = (fin_payoff - fin_premio - custos) if posicao == 'Comprado' else (fin_premio - fin_payoff - custos)
    
    return res, fin_premio, custos, strike

def simular_comparativo(data, params, df_cdi):
    # 1. Executa Trades
    trades = []
    prej_acumulado = 0.0
    
    dias = params['dias']
    qtde = params['qtde']
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    
    indices = [i for i, dt in enumerate(data.index) if dt.date() >= params['inicio'] and dt.date() <= params['fim']]
    if not indices: return None, pd.DataFrame(), "Intervalo inválido"
    
    curr = indices[0]
    limit = len(data) - dias
    
    while curr < limit:
        if data.index[curr].date() > params['fim']: break
        try:
            dt_ent = data.index[curr]
            dt_sai = data.index[curr + dias]
            pr_ent = float(data[col].iloc[curr])
            pr_sai = float(data[col].iloc[curr + dias])
            
            res_op = 0.0
            custos = 0.0
            fluxo_ini = 0.0
            strikes = []
            
            for leg in params['legs']:
                r, p_val, c, k = calcular_leg(pr_ent, pr_sai, qtde, leg['tipo'], leg['posicao'], leg['offset'], leg['premio'])
                res_op += r
                custos += c
                strikes.append(f"{leg['tipo'][0]}{k:.2f}")
                fluxo_ini += p_val if leg['posicao'] == 'Vendido' else -p_val
            
            # IR
            ir = 0.0
            if res_op > 0:
                base = max(0, res_op - prej_acumulado)
                prej_acumulado -= (res_op - base)
                ir = base * 0.15
            else:
                prej_acumulado += abs(res_op)
            
            trades.append({
                'Data': dt_sai, # Data de realização do lucro
                'Res_Liquido': res_op - ir
            })
            
        except: pass
        curr += dias
        
    df_trades = pd.DataFrame(trades)
    if df_trades.empty: return None, pd.DataFrame(), "Nenhuma operação gerada"
    
    # 2. Curva de Patrimônio (Estratégia) vs CDI
    # Cria range de datas completo
    dt_range = pd.date_range(start=params['inicio'], end=params['fim'], freq='B') # Business days
    df_compare = pd.DataFrame(index=dt_range)
    
    # Mapeia CDI
    df_compare['Fator_CDI'] = 1.0
    if not df_cdi.empty:
        # Join com CDI do BC
        df_temp = df_cdi.loc[df_cdi.index.isin(dt_range)]
        df_compare.loc[df_temp.index, 'Fator_CDI'] = df_temp['fator']
    
    # Preenche vazios do CDI com 1.0
    df_compare['Fator_CDI'].fillna(1.0, inplace=True)
    
    # Calcula Curva CDI (Juros Compostos)
    capital = params['capital']
    df_compare['Patrimonio_CDI'] = capital * df_compare['Fator_CDI'].cumprod()
    
    # Calcula Curva Estratégia (Soma Simples dos Resultados ao Capital)
    # Agrupa trades por data de saída
    df_sum_trades = df_trades.groupby('Data')['Res_Liquido'].sum()
    
    df_compare['Trade_Result'] = 0.0
    # Mapeia resultados nas datas corretas (usando índice datetime)
    comuns = df_compare.index.intersection(df_sum_trades.index)
    df_compare.loc[comuns, 'Trade_Result'] = df_sum_trades.loc[comuns]
    
    # Acumula
    df_compare['Patrimonio_Estrat'] = capital + df_compare['Trade_Result'].cumsum()
    
    return df_compare, df_trades, None

# --- INTERFACE ---
st.sidebar.header("🔧 Configuração")

with st.spinner("Carregando CDI..."):
    df_cdi = pegar_cdi_bcb()

# Inputs Principais
ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
capital = st.sidebar.number_input("Capital Inicial (R$)", 1000.0, 1000000.0, 50000.0, 1000.0, help="Valor usado para o Benchmark do CDI")
qtde = st.sidebar.number_input("Lote Opções", 100, 100000, 1000, 100)
dias = st.sidebar.slider("Vencimento (Dias)", 5, 60, 20)

st.sidebar.markdown("---")

# Pernas
c1, c2 = st.sidebar.columns(2)
t1 = c1.selectbox("Tipo P1", ['Call', 'Put'], key='t1')
p1 = c2.selectbox("Ação P1", ['Comprado', 'Vendido'], key='p1')
o1 = st.sidebar.slider("Strike P1 (%)", -20.0, 20.0, 0.0, 0.5, key='o1')
pr1 = st.sidebar.slider("Custo P1 (%)", 0.1, 10.0, 3.0, 0.1, key='c1')

st.sidebar.markdown("---")
use_p2 = st.sidebar.checkbox("Perna 2")
t2, p2, o2, pr2 = None, None, 0.0, 0.0
if use_p2:
    c3, c4 = st.sidebar.columns(2)
    t2 = c3.selectbox("Tipo P2", ['Call', 'Put'], key='t2')
    p2 = c4.selectbox("Ação P2", ['Comprado', 'Vendido'], key='p2', index=1)
    o2 = st.sidebar.slider("Strike P2 (%)", -20.0, 20.0, 5.0, 0.5, key='o2')
    pr2 = st.sidebar.slider("Custo P2 (%)", 0.1, 10.0, 1.5, 0.1, key='c2')

st.sidebar.markdown("---")
ini = st.sidebar.date_input("Início", date.today() - timedelta(days=365*2))
fim = st.sidebar.date_input("Fim", date.today())

if st.sidebar.button("🚀 Comparar com CDI", type="primary"):
    with st.spinner("Processando..."):
        df_dados = baixar_dados(ticker, ini, fim)
        
    if df_dados.empty:
        st.error("Sem dados.")
    else:
        legs = [{'tipo': t1, 'posicao': p1, 'offset': o1, 'premio': pr1}]
        if use_p2: legs.append({'tipo': t2, 'posicao': p2, 'offset': o2, 'premio': pr2})
            
        params = {
            'ticker': ticker, 'capital': capital, 'qtde': qtde, 'dias': dias,
            'inicio': ini, 'fim': fim, 'legs': legs
        }
        
        df_comp, df_ops, erro = simular_comparativo(df_dados, params, df_cdi)
        
        if erro: st.warning(erro)
        else:
            # Cálculos Finais
            saldo_final_estrat = df_comp['Patrimonio_Estrat'].iloc[-1]
            saldo_final_cdi = df_comp['Patrimonio_CDI'].iloc[-1]
            lucro_estrat = saldo_final_estrat - capital
            lucro_cdi = saldo_final_cdi - capital
            
            perf_estrat_pct = ((saldo_final_estrat / capital) - 1) * 100
            perf_cdi_pct = ((saldo_final_cdi / capital) - 1) * 100
            
            cor_estrat = "positive" if lucro_estrat > 0 else "negative"
            delta_vs_cdi = perf_estrat_pct - perf_cdi_pct
            cor_delta = "positive" if delta_vs_cdi > 0 else "negative"
            
            st.subheader("🏆 Comparativo de Performance")
            
            st.markdown(f"""
            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Opções (Saldo Final)</div>
                    <div class="metric-value {cor_estrat}">R$ {saldo_final_estrat:,.2f}</div>
                    <div class="metric-sub">Retorno: {perf_estrat_pct:.1f}%</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Benchmark CDI</div>
                    <div class="metric-value info">R$ {saldo_final_cdi:,.2f}</div>
                    <div class="metric-sub">Retorno: {perf_cdi_pct:.1f}%</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Opções vs CDI</div>
                    <div class="metric-value {cor_delta}">{delta_vs_cdi:+.1f}%</div>
                    <div class="metric-sub">Diferencial de Alpha</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Gráfico Comparativo
            st.markdown("### 📈 Evolução Patrimonial")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_comp.index, y=df_comp['Patrimonio_Estrat'], name='Estratégia Opções', line=dict(color='blue', width=2)))
            fig.add_trace(go.Scatter(x=df_comp.index, y=df_comp['Patrimonio_CDI'], name='Benchmark CDI', line=dict(color='gray', dash='dot')))
            fig.update_layout(title="Comparação: Capital Aplicado na Estratégia vs. CDI", xaxis_title="Data", yaxis_title="Patrimônio (R$)", height=500, hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
            
            # Tabela de Trades (Opcional, escondido em expander para limpar a tela)
            with st.expander("Ver Lista de Operações Individuais"):
                st.dataframe(df_ops)
