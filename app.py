import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Simulador de Opções (Pro)",
    page_icon="💎",
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
    .metric-sub {
        font-size: 12px;
        color: #888;
        margin-top: 5px;
    }
    .positive { color: #28a745; }
    .negative { color: #dc3545; }
    .warning { color: #ffc107; }
    .info { color: #17a2b8; }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES DE DADOS ---

@st.cache_data(ttl=86400) # Cache de 24h para o CDI
def pegar_cdi_bcb():
    """Baixa o histórico do CDI (Série 12) do Banco Central"""
    try:
        url = 'http://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=csv'
        df = pd.read_csv(url, sep=';')
        
        # Tratamento de dados
        df['data'] = pd.to_datetime(df['data'], format='%d/%m/%Y')
        df['valor'] = df['valor'].str.replace(',', '.').astype(float)
        
        # A série 12 é % ao dia. Convertemos para fator diário: 1 + (taxa/100)
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
    
    taxa_entrada = 0.005
    taxa_exercicio = 0.005
    
    custo_ent = fin_premio * taxa_entrada
    custo_sai = (strike * qtde * taxa_exercicio) if exercido else 0.0
    custos_totais = custo_ent + custo_sai
    
    resultado = 0.0
    if posicao == 'Comprado':
        resultado = fin_payoff - fin_premio - custos_totais
    else:
        resultado = fin_premio - fin_payoff - custos_totais
        
    return resultado, fin_premio, custos_totais, strike

def calcular_estrategia_multipla(data, params, df_cdi):
    ticker = params['ticker']
    qtde = params['qtde']
    dias = params['dias']
    legs = params['legs']
    
    ir_aliquota = 0.15
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    
    # Filtra datas
    indices = [i for i, dt in enumerate(data.index) if dt.date() >= params['inicio'] and dt.date() <= params['fim']]
    if not indices: return None, "Intervalo inválido"
    
    trades = []
    prej_acumulado = 0.0
    limit_idx = len(data) - dias
    curr = indices[0]
    
    while curr < limit_idx:
        if data.index[curr].date() > params['fim']: break
        
        try:
            dt_ent = data.index[curr]
            pr_ent = float(data[col].iloc[curr])
            dt_sai = data.index[curr + dias]
            pr_sai = float(data[col].iloc[curr + dias])
            
            res_op = 0.0
            custos_total = 0.0
            premio_net = 0.0 
            
            str_strikes = []
            
            # 1. Calcula Resultado Operacional das Pernas
            for leg in legs:
                r, p_val, c, k = calcular_leg(
                    pr_ent, pr_sai, qtde, 
                    leg['tipo'], leg['posicao'], leg['offset'], leg['premio']
                )
                res_op += r
                custos_total += c
                str_strikes.append(f"{leg['tipo'][0]}{k:.2f}")
                
                if leg['posicao'] == 'Vendido':
                    premio_net += p_val
                else:
                    premio_net -= p_val
            
            # 2. Cálculo do CDI (Resultado Financeiro)
            # O CDI incide sobre o Fluxo Inicial (premio_net) durante o período
            res_financeiro = 0.0
            if not df_cdi.empty:
                # Pega o subconjunto do CDI entre entrada e saída
                mask_cdi = (df_cdi.index >= dt_ent) & (df_cdi.index < dt_sai)
                fator_acumulado = df_cdi.loc[mask_cdi, 'fator'].prod()
                
                # Se fator for 1 (sem dados), ajusta para não zerar
                if fator_acumulado == 0: fator_acumulado = 1.0
                
                # O ganho/perda é: Valor Inicial * (Fator - 1)
                # Se premio_net positivo (recebeu caixa): Ganha CDI
                # Se premio_net negativo (pagou caixa): "Perde" CDI (Custo Oportunidade)
                res_financeiro = premio_net * (fator_acumulado - 1)

            # 3. Resultado Antes do IR (Operacional + Financeiro)
            # Nota: Para base de cálculo de IR de Opções, geralmente conta-se o operacional.
            # O Financeiro (CDI) é tributado na fonte ou separado. Aqui vamos somar no líquido final para visão gerencial.
            
            # 4. IR (Sobre Operacional)
            ir = 0.0
            if res_op > 0:
                base = max(0, res_op - prej_acumulado)
                prej_acumulado -= (res_op - base)
                ir = base * ir_aliquota
            else:
                prej_acumulado += abs(res_op)
                
            # Líquido Final = Operacional Líquido + Resultado CDI
            liq_final = (res_op - ir) + res_financeiro
            
            trades.append({
                'Entrada': dt_ent, 'Pr_Ent': pr_ent, 
                'Saida': dt_sai, 'Pr_Sai': pr_sai,
                'Strikes': " / ".join(str_strikes),
                'Fluxo Inicial': premio_net,
                'Res. CDI': res_financeiro,
                'Custos': custos_total,
                'Res_Op': res_op, 
                'IR': ir,
                'Liquido Final': liq_final
            })
            
        except Exception as e: pass
        curr += dias
        
    return pd.DataFrame(trades), None

# --- INTERFACE ---
st.sidebar.header("🔧 Montador de Estratégia")

# Carrega CDI no início
with st.spinner("Carregando taxas do Banco Central..."):
    df_cdi = pegar_cdi_bcb()

ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
qtde = st.sidebar.number_input("Lote (Qtde)", 100, 100000, 1000, 100)
dias = st.sidebar.slider("Dias Úteis (Vencimento)", 5, 60, 20)

st.sidebar.markdown("---")

# PERNA 1
st.sidebar.markdown("### 🟢 Perna 1")
c1_p1, c2_p1 = st.sidebar.columns(2)
tipo_p1 = c1_p1.selectbox("Tipo P1", ['Call', 'Put'], key='t1')
pos_p1 = c2_p1.selectbox("Ação P1", ['Comprado', 'Vendido'], key='p1')
off_p1 = st.sidebar.slider("Strike P1 (%)", -20.0, 20.0, 0.0, 0.5, key='o1')
pre_p1 = st.sidebar.slider("Custo P1 (%)", 0.1, 10.0, 3.0, 0.1, key='c1')

# PERNA 2
st.sidebar.markdown("---")
usar_p2 = st.sidebar.checkbox("Adicionar Perna 2")
tipo_p2, pos_p2, off_p2, pre_p2 = None, None, 0.0, 0.0

if usar_p2:
    st.sidebar.markdown("### 🔵 Perna 2")
    c1_p2, c2_p2 = st.sidebar.columns(2)
    tipo_p2 = c1_p2.selectbox("Tipo P2", ['Call', 'Put'], key='t2')
    pos_p2 = c2_p2.selectbox("Ação P2", ['Comprado', 'Vendido'], key='p2', index=1)
    off_p2 = st.sidebar.slider("Strike P2 (%)", -20.0, 20.0, 5.0, 0.5, key='o2')
    pre_p2 = st.sidebar.slider("Custo P2 (%)", 0.1, 10.0, 1.5, 0.1, key='c2')

# DATAS
st.sidebar.markdown("---")
dt_ini = st.sidebar.date_input("Início", date.today() - timedelta(days=365))
dt_fim = st.sidebar.date_input("Fim", date.today())

# EXECUÇÃO
if st.sidebar.button("🚀 Simular", type="primary"):
    with st.spinner("Calculando cenários..."):
        df_dados = baixar_dados(ticker, dt_ini, dt_fim)
        
    if df_dados.empty:
        st.error("Sem dados de cotação.")
    else:
        legs_config = [{'tipo': tipo_p1, 'posicao': pos_p1, 'offset': off_p1, 'premio': pre_p1}]
        if usar_p2:
            legs_config.append({'tipo': tipo_p2, 'posicao': pos_p2, 'offset': off_p2, 'premio': pre_p2})
            
        params = {
            'ticker': ticker, 'qtde': qtde, 'dias': dias,
            'inicio': dt_ini, 'fim': dt_fim, 'legs': legs_config
        }
        
        df, erro = calcular_estrategia_multipla(df_dados, params, df_cdi)
        
        if erro: st.warning(erro)
        elif df.empty: st.warning("Nenhuma operação.")
        else:
            # TOTAIS
            tot_liq = df['Liquido Final'].sum()
            tot_op = df['Res_Op'].sum() - df['IR'].sum() # Liq Operacional
            tot_cdi = df['Res. CDI'].sum()
            win = (len(df[df['Res_Op'] > 0]) / len(df)) * 100
            
            cor = "positive" if tot_liq > 0 else "negative"
            cor_cdi = "positive" if tot_cdi > 0 else "negative" # Pode ser negativo (custo oportunidade)
            
            st.subheader(f"Resultado Consolidado")
            
            st.markdown(f"""
            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Líquido Total (Op + CDI)</div>
                    <div class="metric-value {cor}">R$ {tot_liq:,.2f}</div>
                    <div class="metric-sub">O que sobra no bolso</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Resultado Opções (Pós IR)</div>
                    <div class="metric-value">R$ {tot_op:,.2f}</div>
                    <div class="metric-sub">Performance pura da estratégia</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Resultado CDI</div>
                    <div class="metric-value {cor_cdi}">R$ {tot_cdi:,.2f}</div>
                    <div class="metric-sub">Renda do Caixa ou Custo Oport.</div>
                </div>
                 <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Win Rate</div>
                    <div class="metric-value">{win:.1f}%</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # TABELA
            st.markdown("### 📋 Extrato Detalhado")
            
            cols = ['Entrada', 'Pr_Ent', 'Strikes', 'Saida', 'Pr_Sai', 'Fluxo Inicial', 'Res. CDI', 'Custos', 'Res_Op', 'IR', 'Liquido Final']
            fmt = {c: 'R$ {:.2f}' for c in cols if c not in ['Entrada', 'Saida', 'Strikes']}
            
            df_show = df[cols].copy()
            df_show['Entrada'] = df_show['Entrada'].dt.strftime('%d/%m/%y')
            df_show['Saida'] = df_show['Saida'].dt.strftime('%d/%m/%y')
            
            # Renomear colunas para caber melhor
            df_show.rename(columns={'Liquido Final': 'Líquido', 'Fluxo Inicial': 'Fluxo Ini.'}, inplace=True)
            
            st.dataframe(
                df_show.style.format(fmt)
                       .map(lambda x: 'color: green' if x>0 else 'color: red', subset=['Líquido', 'Res. CDI', 'Res_Op']),
                use_container_width=True,
                height=500
            )
            
            # Download
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Baixar CSV", csv, "backtest_completo.csv", "text/csv")
