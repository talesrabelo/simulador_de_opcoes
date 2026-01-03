import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Simulador de Opções (Custo Oportunidade)",
    page_icon="⏳",
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
    .info { color: #17a2b8; }
</style>
""", unsafe_allow_html=True)

# --- FUNÇÕES ---

@st.cache_data(ttl=86400)
def pegar_cdi_bcb():
    """Baixa o CDI diário do Banco Central"""
    try:
        url = 'http://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=csv'
        df = pd.read_csv(url, sep=';')
        
        # Limpeza
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
    
    # Taxas
    taxa_entrada = 0.005
    taxa_exercicio = 0.005
    
    custo_ent = fin_premio * taxa_entrada
    custo_sai = (strike * qtde * taxa_exercicio) if exercido else 0.0
    custos_totais = custo_ent + custo_sai
    
    resultado = 0.0
    if posicao == 'Comprado':
        resultado = fin_payoff - fin_premio - custos_totais
    else: # Vendido
        resultado = fin_premio - fin_payoff - custos_totais
        
    return resultado, fin_premio, custos_totais, strike

def calcular_estrategia_multipla(data, params, df_cdi):
    ticker = params['ticker']
    qtde = params['qtde']
    dias = params['dias']
    legs = params['legs'] 
    
    ir_aliquota = 0.15
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    
    # Converte data fim da simulação para datetime para comparação
    dt_fim_simulacao = pd.to_datetime(params['fim'])
    
    mask = (data.index.date >= params['inicio']) & (data.index.date <= params['fim'])
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
            
            res_op_total = 0.0
            custos_total = 0.0
            premio_net = 0.0 # Positivo = Recebeu, Negativo = Pagou
            
            str_strikes = []
            
            # 1. Calcula Pernas (Opções)
            for leg in legs:
                r, p_val, c, k = calcular_leg(
                    pr_ent, pr_sai, qtde, 
                    leg['tipo'], leg['posicao'], leg['offset'], leg['premio']
                )
                
                res_op_total += r
                custos_total += c
                str_strikes.append(f"{leg['tipo'][0]}{k:.2f}")
                
                if leg['posicao'] == 'Vendido':
                    premio_net += p_val
                else:
                    premio_net -= p_val
            
            # 2. CÁLCULO DO CDI LONGO (ATÉ O FIM DA SIMULAÇÃO)
            # Regra: Pega o Fluxo Inicial e aplica o CDI da Data de Entrada até a Data Final da Simulação
            res_cdi_longo = 0.0
            
            if not df_cdi.empty:
                # O range agora vai de dt_ent até dt_fim_simulacao (e não mais até dt_sai)
                mask_cdi = (df_cdi.index >= dt_ent) & (df_cdi.index <= dt_fim_simulacao)
                
                if not df_cdi[mask_cdi].empty:
                    # Acumula o fator de todo esse período longo
                    fator_acumulado = df_cdi.loc[mask_cdi, 'fator'].prod()
                    
                    # Se Fluxo Negativo (Pagou): Mostra quanto deixaria de ganhar (Custo Oportunidade)
                    # Se Fluxo Positivo (Recebeu): Mostra quanto renderia esse caixa no longo prazo
                    res_cdi_longo = premio_net * (fator_acumulado - 1)

            # 3. IR (Sobre Operacional)
            ir = 0.0
            if res_op_total > 0:
                base = max(0, res_op_total - prej_acumulado)
                prej_acumulado -= (res_op_total - base)
                ir = base * ir_aliquota
            else:
                prej_acumulado += abs(res_op_total)
            
            # Líquido = Resultado Opções + Resultado CDI Longo - IR
            # Obs: Aqui estamos somando grandezas temporais diferentes para efeito de comparação gerencial
            liq = res_op_total + res_cdi_longo - ir
            
            trades.append({
                'Entrada': dt_ent, 
                'Saida Trade': dt_sai, 
                'Fim Simulação': dt_fim_simulacao, # Apenas para referência
                'Pr_Ent': pr_ent, 'Pr_Sai': pr_sai,
                'Strikes': " / ".join(str_strikes),
                'Fluxo Inicial': premio_net, 
                'CDI Total (Até Fim)': res_cdi_longo,    # Coluna Solicitada Ajustada
                'Custos': custos_total,
                'Res_Op': res_op_total, 
                'Liquido': liq
            })
            
        except Exception as e: pass
        curr += dias
        
    return pd.DataFrame(trades), None

# --- INTERFACE ---
st.sidebar.header("🔧 Montador de Estratégia")

# Carrega CDI
with st.spinner("Carregando CDI do Banco Central..."):
    df_cdi = pegar_cdi_bcb()

ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
qtde = st.sidebar.number_input("Lote (Qtde)", 100, 100000, 1000, 100)
dias = st.sidebar.slider("Dias Úteis (Vencimento Opção)", 5, 60, 20)

st.sidebar.markdown("---")

# PERNA 1
c1_p1, c2_p1 = st.sidebar.columns(2)
tipo_p1 = c1_p1.selectbox("Tipo P1", ['Call', 'Put'], key='t1')
pos_p1 = c2_p1.selectbox("Ação P1", ['Comprado', 'Vendido'], key='p1')
off_p1 = st.sidebar.slider("Strike P1 vs Preço (%)", -20.0, 20.0, 0.0, 0.5, key='o1')
pre_p1 = st.sidebar.slider("Custo P1 (% do Ativo)", 0.1, 10.0, 3.0, 0.1, key='c1')

# PERNA 2
st.sidebar.markdown("---")
usar_p2 = st.sidebar.checkbox("Adicionar Perna 2")
tipo_p2, pos_p2, off_p2, pre_p2 = None, None, 0.0, 0.0

if usar_p2:
    c1_p2, c2_p2 = st.sidebar.columns(2)
    tipo_p2 = c1_p2.selectbox("Tipo P2", ['Call', 'Put'], key='t2')
    pos_p2 = c2_p2.selectbox("Ação P2", ['Comprado', 'Vendido'], key='p2', index=1)
    off_p2 = st.sidebar.slider("Strike P2 vs Preço (%)", -20.0, 20.0, 5.0, 0.5, key='o2')
    pre_p2 = st.sidebar.slider("Custo P2 (% do Ativo)", 0.1, 10.0, 1.5, 0.1, key='c2')

# DATAS
st.sidebar.markdown("---")
dt_ini = st.sidebar.date_input("Início Simulação", date.today() - timedelta(days=365))
dt_fim = st.sidebar.date_input("Fim Simulação (Data Base CDI)", date.today())

# EXECUÇÃO
if st.sidebar.button("🚀 Simular", type="primary"):
    with st.spinner("Calculando..."):
        df_dados = baixar_dados(ticker, dt_ini, dt_fim)
        
    if df_dados.empty:
        st.error("Sem dados.")
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
            # Cards
            tot_liq = df['Liquido'].sum()
            tot_cdi = df['CDI Total (Até Fim)'].sum()
            tot_cust = df['Custos'].sum()
            win = (len(df[df['Res_Op'] > 0]) / len(df)) * 100
            
            cor = "positive" if tot_liq > 0 else "negative"
            cor_cdi = "positive" if tot_cdi >= 0 else "neutral"
            
            st.subheader(f"Resultado Acumulado")
            st.caption(f"Nota: O valor do CDI reflete a aplicação do Fluxo Inicial desde a entrada de cada trade até a data final ({dt_fim.strftime('%d/%m/%Y')}).")
            
            st.markdown(f"""
            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Resultado Líquido</div>
                    <div class="metric-value {cor}">R$ {tot_liq:,.2f}</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">CDI Acumulado (Total)</div>
                    <div class="metric-value {cor_cdi}">R$ {tot_cdi:,.2f}</div>
                </div>
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Custos Operacionais</div>
                    <div class="metric-value warning">R$ {tot_cust:,.2f}</div>
                </div>
                 <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Taxa de Acerto</div>
                    <div class="metric-value">{win:.1f}%</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Tabela
            cols = ['Entrada', 'Pr_Ent', 'Strikes', 'Saida Trade', 'Pr_Sai', 'Fluxo Inicial', 'CDI Total (Até Fim)', 'Custos', 'Res_Op', 'Liquido']
            fmt = {c: 'R$ {:.2f}' for c in cols if c not in ['Entrada', 'Saida Trade', 'Strikes']}
            
            df_show = df[cols].copy()
            df_show['Entrada'] = df_show['Entrada'].dt.strftime('%d/%m/%Y')
            df_show['Saida Trade'] = df_show['Saida Trade'].dt.strftime('%d/%m/%Y')
            
            st.dataframe(
                df_show.style.format(fmt).map(lambda x: 'color: green' if x>0 else 'color: red', subset=['Res_Op', 'Liquido', 'CDI Total (Até Fim)']),
                use_container_width=True,
                height=500
            )
