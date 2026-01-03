import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Opções vs Renda Fixa",
    page_icon="💰",
    layout="wide"
)

warnings.simplefilter(action='ignore', category=FutureWarning)

# --- ESTILO CSS (Visual Limpo) ---
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
        font-size: 28px;
        font-weight: bold;
        margin-top: 5px;
    }
    .positive { color: #28a745; }
    .negative { color: #dc3545; }
    .neutral { color: #6c757d; }
    .info { color: #17a2b8; }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES ---

@st.cache_data(ttl=86400)
def pegar_cdi_bcb():
    """Baixa CDI diário do Banco Central"""
    try:
        url = 'http://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=csv'
        df = pd.read_csv(url, sep=';')
        df['data'] = pd.to_datetime(df['data'], format='%d/%m/%Y')
        df['valor'] = df['valor'].str.replace(',', '.').astype(float)
        # Fator diário = 1 + (taxa/100)
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
        
        # Limpeza MultiIndex
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
    
    # Taxas: 0.5% entrada (premio) + 0.5% saida (strike se exercido)
    taxa_ent = fin_premio * 0.005
    taxa_sai = (strike * qtde * 0.005) if exercido else 0.0
    custos = taxa_ent + taxa_sai
    
    res = (fin_payoff - fin_premio - custos) if posicao == 'Comprado' else (fin_premio - fin_payoff - custos)
    
    return res, custos

def simular_comparativo(data, params, df_cdi):
    capital_atual_estrat = params['capital'] # Começa com R$ X
    
    # Cria a curva do CDI (Universo B)
    # Se df_cdi existir, filtramos pelo período e calculamos o acumulado
    fator_acumulado_cdi = 1.0
    if not df_cdi.empty:
        # Pega CDI desde o início até o fim da simulação
        mask_cdi = (df_cdi.index >= pd.to_datetime(params['inicio'])) & (df_cdi.index <= pd.to_datetime(params['fim']))
        fator_acumulado_cdi = df_cdi.loc[mask_cdi, 'fator'].prod()
    
    capital_final_cdi = params['capital'] * fator_acumulado_cdi
    
    # Simula a Estratégia (Universo A)
    trades = []
    prej_acumulado = 0.0
    
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    indices = [i for i, dt in enumerate(data.index) if dt.date() >= params['inicio'] and dt.date() <= params['fim']]
    
    if not indices: return None, "Intervalo inválido"
    
    curr = indices[0]
    limit = len(data) - params['dias']
    
    while curr < limit:
        if data.index[curr].date() > params['fim']: break
        try:
            # Dados Trade
            dt_ent = data.index[curr]
            dt_sai = data.index[curr + params['dias']]
            pr_ent = float(data[col].iloc[curr])
            pr_sai = float(data[col].iloc[curr + params['dias']] )
            
            res_bruto_trade = 0.0
            custos_trade = 0.0
            
            # Calcula Pernas
            for leg in params['legs']:
                r, c = calcular_leg(pr_ent, pr_sai, params['qtde'], leg['tipo'], leg['posicao'], leg['offset'], leg['premio'])
                res_bruto_trade += r
                custos_trade += c
            
            # IR
            ir = 0.0
            if res_bruto_trade > 0:
                base = max(0, res_bruto_trade - prej_acumulado)
                prej_acumulado -= (res_bruto_trade - base)
                ir = base * 0.15
            else:
                prej_acumulado += abs(res_bruto_trade)
            
            liq_trade = res_bruto_trade - ir
            
            # Atualiza o Capital da Estratégia (Juros Simples sobre o saldo anterior ou apenas soma ao caixa)
            # Modelo: O lucro entra no caixa, o prejuízo sai do caixa.
            capital_atual_estrat += liq_trade
            
            trades.append({
                'Data Saída': dt_sai,
                'Resultado Op.': liq_trade,
                'Saldo Acumulado': capital_atual_estrat
            })
            
        except: pass
        curr += params['dias']
    
    return {
        'saldo_opcoes': capital_atual_estrat,
        'saldo_cdi': capital_final_cdi,
        'trades': pd.DataFrame(trades)
    }, None

# --- INTERFACE ---
st.sidebar.header("🔧 Configuração")

with st.spinner("Carregando CDI..."):
    df_cdi = pegar_cdi_bcb()

# Inputs
ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
capital = st.sidebar.number_input("Capital Inicial (R$)", 1000.0, 1000000.0, 50000.0, 1000.0)
qtde = st.sidebar.number_input("Lote Opções", 100, 100000, 1000, 100)
dias = st.sidebar.slider("Vencimento (Dias)", 5, 60, 20)

st.sidebar.markdown("---")
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

if st.sidebar.button("🚀 Comparar Resultados", type="primary"):
    with st.spinner("Calculando..."):
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
        
        res, erro = simular_comparativo(df_dados, params, df_cdi)
        
        if erro: st.warning(erro)
        elif res['trades'].empty: st.warning("Nenhuma operação gerada.")
        else:
            # RESULTADOS FINAIS
            s_op = res['saldo_opcoes']
            s_cdi = res['saldo_cdi']
            
            # Rentabilidade %
            rent_op = ((s_op / capital) - 1) * 100
            rent_cdi = ((s_cdi / capital) - 1) * 100
            diff = s_op - s_cdi
            
            cor_op = "positive" if rent_op > 0 else "negative"
            cor_diff = "positive" if diff > 0 else "negative"
            
            st.subheader(f"Comparativo Final: R$ {capital:,.2f} aplicados em {ini.strftime('%d/%m/%Y')}")
            
            st.markdown(f"""
            <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 30px;">
                <div class="metric-card" style="flex: 1; border-top: 5px solid blue;">
                    <div class="metric-label">Saldo Opções</div>
                    <div class="metric-value {cor_op}">R$ {s_op:,.2f}</div>
                    <div class="metric-label" style="margin-top:10px;">Retorno: {rent_op:.1f}%</div>
                </div>
                <div class="metric-card" style="flex: 1; border-top: 5px solid gray;">
                    <div class="metric-label">Saldo 100% CDI</div>
                    <div class="metric-value info">R$ {s_cdi:,.2f}</div>
                    <div class="metric-label" style="margin-top:10px;">Retorno: {rent_cdi:.1f}%</div>
                </div>
                <div class="metric-card" style="flex: 1; border-top: 5px solid {('green' if diff > 0 else 'red')};">
                    <div class="metric-label">Diferença (Bolso)</div>
                    <div class="metric-value {cor_diff}">R$ {diff:+,.2f}</div>
                    <div class="metric-label" style="margin-top:10px;">Opções vs CDI</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown("### 📋 Evolução da Conta Opções")
            df_show = res['trades'].copy()
            df_show['Data Saída'] = pd.to_datetime(df_show['Data Saída']).dt.strftime('%d/%m/%Y')
            
            st.dataframe(
                df_show.style.format({
                    'Resultado Op.': 'R$ {:.2f}',
                    'Saldo Acumulado': 'R$ {:.2f}'
                }).map(lambda x: 'color: green' if x>0 else 'color: red', subset=['Resultado Op.']),
                use_container_width=True,
                height=500
            )
