import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Simulador de Opções Pro",
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
        font-size: 26px;
        font-weight: bold;
        margin-top: 5px;
    }
    .positive { color: #28a745; }
    .negative { color: #dc3545; }
    .warning { color: #ffc107; }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES ---
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

def calcular_resultado(data, params):
    ticker = params['ticker']
    qtde = params['qtde']
    posicao = params['posicao']
    tipo = params['tipo']
    dias = params['dias']
    premio_pct = params['premio_pct'] / 100.0
    offset_pct = params['offset_pct'] / 100.0
    
    # Custos B3
    taxa_entrada = 0.005
    taxa_exercicio = 0.005
    ir_aliquota = 0.15
    
    # Validação Coluna
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    if col not in data.columns: return None, "Preço não encontrado"

    # Filtro Datas
    mask = (data.index.date >= params['inicio']) & (data.index.date <= params['fim'])
    df_sim = data.loc[mask]
    if df_sim.empty: return None, "Sem dados no período"
    
    # Índices Válidos
    indices = [i for i, dt in enumerate(data.index) if dt.date() >= params['inicio'] and dt.date() <= params['fim']]
    if not indices: return None, "Intervalo inválido"
    
    trades = []
    prej_acumulado = 0.0
    limit_idx = len(data) - dias
    curr = indices[0]
    
    while curr < limit_idx:
        if data.index[curr].date() > params['fim']: break
        
        try:
            # Dados Mercado
            dt_ent = data.index[curr]
            pr_ent = float(data[col].iloc[curr])
            
            dt_sai = data.index[curr + dias]
            pr_sai = float(data[col].iloc[curr + dias])
            
            # Definição Strikes
            strike_call = 0.0
            strike_put = 0.0
            
            if tipo == 'Straddle':
                # Simétrico
                strike_call = pr_ent * (1 + abs(offset_pct))
                strike_put = pr_ent * (1 - abs(offset_pct))
            else:
                # Direcional (Respeita o sinal negativo/positivo)
                strike_unico = pr_ent * (1 + offset_pct)
                if tipo == 'Call': strike_call = strike_unico
                if tipo == 'Put': strike_put = strike_unico
                
            # Pernas Ativas
            usa_call = (tipo == 'Call' or tipo == 'Straddle')
            usa_put = (tipo == 'Put' or tipo == 'Straddle')
            
            # 1. Custo Inicial (Prêmio Pago ou Recebido)
            fin_premio = 0.0
            if usa_call: fin_premio += (pr_ent * premio_pct) * qtde
            if usa_put: fin_premio += (pr_ent * premio_pct) * qtde
            
            custo_ent = fin_premio * taxa_entrada
            
            # 2. Payoff Final e Exercício
            payoff_tot = 0.0
            custo_exe = 0.0
            
            if usa_call:
                val = max(0, pr_sai - strike_call)
                payoff_tot += val * qtde
                if pr_sai > strike_call:
                    custo_exe += (strike_call * qtde) * taxa_exercicio
            
            if usa_put:
                val = max(0, strike_put - pr_sai)
                payoff_tot += val * qtde
                if pr_sai < strike_put:
                    custo_exe += (strike_put * qtde) * taxa_exercicio
            
            custos_tot = custo_ent + custo_exe
            
            # 3. Resultado
            if posicao == 'Comprado':
                res = payoff_tot - fin_premio - custos_tot
            else:
                res = fin_premio - payoff_tot - custos_tot
                
            # 4. IR
            ir = 0.0
            if res > 0:
                base = max(0, res - prej_acumulado)
                prej_acumulado -= (res - base)
                ir = base * ir_aliquota
            else:
                prej_acumulado += abs(res)
                
            liq = res - ir
            
            # Strike para exibição
            strike_show = strike_call if tipo == 'Call' else (strike_put if tipo == 'Put' else 0)
            
            trades.append({
                'Entrada': dt_ent, 'Pr_Ent': pr_ent, 'Strike': strike_show,
                'Saida': dt_sai, 'Pr_Sai': pr_sai,
                'Premio': fin_premio, 'Custos': custos_tot,
                'Res_Op': res, 'IR': ir, 'Liquido': liq
            })
            
        except: pass
        curr += dias
        
    return pd.DataFrame(trades), None

# --- INTERFACE ---
st.sidebar.header("⚙️ Configuração")

ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
qtde = st.sidebar.number_input("Lote", 100, 100000, 1000, step=100)

st.sidebar.markdown("---")
tipo = st.sidebar.selectbox("Estratégia", ['Call', 'Put', 'Straddle'])
posicao = st.sidebar.selectbox("Posição", ['Comprado', 'Vendido'])

# DATA
st.sidebar.markdown("---")
c1, c2 = st.sidebar.columns(2)
ini = c1.date_input("Início", date.today() - timedelta(days=365))
fim = c2.date_input("Fim", date.today())

# PARÂMETROS OPÇÃO
st.sidebar.markdown("---")
st.sidebar.caption("Parâmetros da Opção")

# Reorganizei a ordem para o Strike aparecer antes do Prazo (fica mais visível)
offset = st.sidebar.slider("Strike vs Preço Atual (%)", -20.0, 20.0, 0.0, 0.5, help="0% = ATM. Positivo = Acima. Negativo = Abaixo.")
premio = st.sidebar.slider("Prêmio p/ Perna (%)", 0.1, 10.0, 3.0, 0.1)
dias = st.sidebar.slider("Dias Vencimento", 5, 60, 20)

if st.sidebar.button("🚀 Simular", type="primary"):
    with st.spinner(f"Processando {ticker}..."):
        df_dados = baixar_dados(ticker, ini, fim)
        
    if df_dados.empty:
        st.error("Erro ao baixar dados.")
    else:
        params = {
            'ticker': ticker, 'qtde': qtde, 'posicao': posicao,
            'tipo': tipo, 'inicio': ini, 'fim': fim,
            'dias': dias, 'premio_pct': premio, 'offset_pct': offset
        }
        
        df, erro = calcular_resultado(df_dados, params)
        
        if erro: st.warning(erro)
        elif df.empty: st.warning("Nenhuma operação gerada.")
        else:
            # DASHBOARD
            tot_liq = df['Liquido'].sum()
            tot_cus = df['Custos'].sum()
            win = (len(df[df['Res_Op'] > 0]) / len(df)) * 100
            
            cor = "positive" if tot_liq > 0 else "negative"
            
            st.markdown(f"""
            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Resultado Líquido</div>
                    <div class="metric-value {cor}">R$ {tot_liq:,.2f}</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Custos Totais</div>
                    <div class="metric-value warning">R$ {tot_cus:,.2f}</div>
                </div>
                 <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Win Rate</div>
                    <div class="metric-value">{win:.1f}%</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # TABELA
            cols = ['Entrada', 'Pr_Ent', 'Strike', 'Saida', 'Pr_Sai', 'Premio', 'Custos', 'Res_Op', 'Liquido']
            df_show = df[cols].copy()
            
            # Remove Strike visual se for Straddle
            if tipo == 'Straddle': df_show.drop(columns=['Strike'], inplace=True)
            
            # Formatação
            fmt = {c: 'R$ {:.2f}' for c in ['Pr_Ent', 'Strike', 'Pr_Sai', 'Premio', 'Custos', 'Res_Op', 'Liquido'] if c in df_show.columns}
            df_show['Entrada'] = df_show['Entrada'].dt.strftime('%d/%m/%y')
            df_show['Saida'] = df_show['Saida'].dt.strftime('%d/%m/%y')
            
            st.dataframe(
                df_show.style.format(fmt).map(lambda x: 'color: green' if x>0 else 'color: red', subset=['Res_Op', 'Liquido']),
                use_container_width=True,
                height=500
            )
