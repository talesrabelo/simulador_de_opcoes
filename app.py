import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings
import io # Necessário para criar o arquivo Excel na memória

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(
    page_title="Simulador de Opções (Builder)",
    page_icon="🔧",
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
    """Calcula o resultado financeiro de uma única perna"""
    
    # 1. Definição do Strike
    strike = pr_ent * (1 + offset_pct/100.0)
    
    # 2. Custo Inicial (Prêmio)
    premio_un = pr_ent * (premio_pct/100.0)
    fin_premio = premio_un * qtde
    
    # 3. Payoff (Valor no Vencimento)
    payoff_un = 0.0
    exercido = False
    
    if tipo == 'Call':
        payoff_un = max(0, pr_sai - strike)
        if pr_sai > strike: exercido = True
    elif tipo == 'Put':
        payoff_un = max(0, strike - pr_sai)
        if pr_sai < strike: exercido = True
        
    fin_payoff = payoff_un * qtde
    
    # 4. Custos Operacionais (B3 + Corretagem Estimada)
    taxa_entrada = 0.005 # 0.5% sobre prêmio
    taxa_exercicio = 0.005 # 0.5% sobre strike (pesado!)
    
    custo_ent = fin_premio * taxa_entrada
    custo_sai = (strike * qtde * taxa_exercicio) if exercido else 0.0
    custos_totais = custo_ent + custo_sai
    
    # 5. Resultado da Perna
    resultado = 0.0
    
    if posicao == 'Comprado':
        # Sai Prêmio, Entra Payoff
        resultado = fin_payoff - fin_premio - custos_totais
    else: # Vendido
        # Entra Prêmio, Sai Payoff
        resultado = fin_premio - fin_payoff - custos_totais
        
    return resultado, fin_premio, custos_totais, strike

def calcular_estrategia_multipla(data, params):
    ticker = params['ticker']
    qtde = params['qtde']
    dias = params['dias']
    legs = params['legs'] # Lista de dicionários com config das pernas
    
    ir_aliquota = 0.15
    col = 'Close' if 'Close' in data.columns else 'Adj Close'
    
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
            
            res_total = 0.0
            custos_total = 0.0
            premio_net = 0.0 # Positivo = Recebeu, Negativo = Pagou
            
            str_strikes = []
            
            # Itera sobre as pernas ativas
            for leg in legs:
                r, p_val, c, k = calcular_leg(
                    pr_ent, pr_sai, qtde, 
                    leg['tipo'], leg['posicao'], leg['offset'], leg['premio']
                )
                
                res_total += r
                custos_total += c
                str_strikes.append(f"{leg['tipo'][0]}{k:.2f}")
                
                if leg['posicao'] == 'Vendido':
                    premio_net += p_val
                else:
                    premio_net -= p_val
            
            # IR
            ir = 0.0
            if res_total > 0:
                base = max(0, res_total - prej_acumulado)
                prej_acumulado -= (res_total - base)
                ir = base * ir_aliquota
            else:
                prej_acumulado += abs(res_total)
                
            liq = res_total - ir
            
            trades.append({
                'Entrada': dt_ent, 'Pr_Ent': pr_ent, 
                'Saida': dt_sai, 'Pr_Sai': pr_sai,
                'Strikes': " / ".join(str_strikes),
                'Fluxo Inicial': premio_net, # Quanto pagou ou recebeu na montagem
                'Custos': custos_total,
                'Res_Op': res_total, 'Liquido': liq
            })
            
        except Exception as e: pass
        curr += dias
        
    return pd.DataFrame(trades), None

# --- FUNÇÃO PARA GERAR EXCEL FORMATADO ---
def to_excel_formatado(df):
    output = io.BytesIO()
    # Usa o engine xlsxwriter para permitir formatação avançada
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Resultado')
        workbook = writer.book
        worksheet = writer.sheets['Resultado']
        
        # Formatos
        money_fmt = workbook.add_format({'num_format': 'R$ #,##0.00'})
        date_fmt = workbook.add_format({'num_format': 'dd/mm/yyyy'})
        
        # Aplica formatação nas colunas
        # Colunas de Data (A e D geralmente, mas vamos pelo nome)
        for i, col in enumerate(df.columns):
            # Define largura padrão
            worksheet.set_column(i, i, 15)
            
            if col in ['Entrada', 'Saida']:
                worksheet.set_column(i, i, 12, date_fmt)
            elif col in ['Pr_Ent', 'Pr_Sai', 'Fluxo Inicial', 'Custos', 'Res_Op', 'Liquido']:
                worksheet.set_column(i, i, 18, money_fmt)
                
    return output.getvalue()

# --- INTERFACE ---
st.sidebar.header("🔧 Montador de Estratégia")

ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
qtde = st.sidebar.number_input("Lote (Qtde)", 100, 100000, 1000, 100)
dias = st.sidebar.slider("Dias Úteis (Vencimento)", 5, 60, 20)

st.sidebar.markdown("---")

# --- PERNA 1 ---
st.sidebar.markdown("### 🟢 Perna 1 (Principal)")
c1_p1, c2_p1 = st.sidebar.columns(2)
tipo_p1 = c1_p1.selectbox("Tipo P1", ['Call', 'Put'], key='t1')
pos_p1 = c2_p1.selectbox("Ação P1", ['Comprado', 'Vendido'], key='p1')
off_p1 = st.sidebar.slider("Strike P1 vs Preço (%)", -20.0, 20.0, 0.0, 0.5, key='o1')
pre_p1 = st.sidebar.slider("Custo P1 (% do Ativo)", 0.1, 10.0, 3.0, 0.1, key='c1')

# --- PERNA 2 ---
st.sidebar.markdown("---")
usar_p2 = st.sidebar.checkbox("Adicionar Perna 2 (Combinada)")
tipo_p2, pos_p2, off_p2, pre_p2 = None, None, 0.0, 0.0

if usar_p2:
    st.sidebar.markdown("### 🔵 Perna 2 (Secundária)")
    c1_p2, c2_p2 = st.sidebar.columns(2)
    tipo_p2 = c1_p2.selectbox("Tipo P2", ['Call', 'Put'], key='t2')
    pos_p2 = c2_p2.selectbox("Ação P2", ['Comprado', 'Vendido'], key='p2', index=1) # Padrão Vendido para facilitar Travas
    off_p2 = st.sidebar.slider("Strike P2 vs Preço (%)", -20.0, 20.0, 5.0, 0.5, key='o2')
    pre_p2 = st.sidebar.slider("Custo P2 (% do Ativo)", 0.1, 10.0, 1.5, 0.1, key='c2')

# --- DATAS ---
st.sidebar.markdown("---")
dt_ini = st.sidebar.date_input("Início", date.today() - timedelta(days=365))
dt_fim = st.sidebar.date_input("Fim", date.today())

# --- EXECUÇÃO ---
if st.sidebar.button("🚀 Simular Combinação", type="primary"):
    with st.spinner("Processando..."):
        df_dados = baixar_dados(ticker, dt_ini, dt_fim)
        
    if df_dados.empty:
        st.error("Sem dados.")
    else:
        # Monta lista de pernas
        legs_config = [{
            'tipo': tipo_p1, 'posicao': pos_p1, 
            'offset': off_p1, 'premio': pre_p1
        }]
        
        desc_estrat = f"{pos_p1} {tipo_p1} ({off_p1}%)"
        
        if usar_p2:
            legs_config.append({
                'tipo': tipo_p2, 'posicao': pos_p2, 
                'offset': off_p2, 'premio': pre_p2
            })
            desc_estrat += f" + {pos_p2} {tipo_p2} ({off_p2}%)"
            
        params = {
            'ticker': ticker, 'qtde': qtde, 'dias': dias,
            'inicio': dt_ini, 'fim': dt_fim, 'legs': legs_config
        }
        
        df, erro = calcular_estrategia_multipla(df_dados, params)
        
        if erro: st.warning(erro)
        elif df.empty: st.warning("Nenhuma operação.")
        else:
            # Cards
            tot_liq = df['Liquido'].sum()
            tot_cust = df['Custos'].sum()
            win = (len(df[df['Res_Op'] > 0]) / len(df)) * 100
            cor = "positive" if tot_liq > 0 else "negative"
            
            st.subheader(f"Resultado: {desc_estrat}")
            
            st.markdown(f"""
            <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;">
                <div class="metric-card" style="flex: 1;">
                    <div class="metric-label">Resultado Líquido</div>
                    <div class="metric-value {cor}">R$ {tot_liq:,.2f}</div>
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
            
            # Seleção de Colunas
            cols = ['Entrada', 'Pr_Ent', 'Strikes', 'Saida', 'Pr_Sai', 'Fluxo Inicial', 'Custos', 'Res_Op', 'Liquido']
            
            # --- EXIBIÇÃO NA TELA (STREAMLIT) ---
            # Aqui criamos uma cópia formatada em TEXTO apenas para exibição
            df_display = df[cols].copy()
            fmt = {c: 'R$ {:.2f}' for c in ['Pr_Ent', 'Pr_Sai', 'Fluxo Inicial', 'Custos', 'Res_Op', 'Liquido']}
            
            # Formata datas como texto para ficar bonito na tela
            df_display['Entrada'] = df_display['Entrada'].dt.strftime('%d/%m/%Y')
            df_display['Saida'] = df_display['Saida'].dt.strftime('%d/%m/%Y')
            
            st.dataframe(
                df_display.style.format(fmt).map(lambda x: 'color: green' if x>0 else 'color: red', subset=['Res_Op', 'Liquido']),
                use_container_width=True,
                height=500
            )

            # --- BOTÃO DE DOWNLOAD EXCEL FORMATADO ---
            # Aqui usamos o DF original (com números e datas reais) e aplicamos formatação do Excel
            df_excel = df[cols].copy() # Pega os dados originais (numéricos)
            
            excel_data = to_excel_formatado(df_excel)
            
            st.download_button(
                label="📥 Baixar Planilha Excel (.xlsx)",
                data=excel_data,
                file_name=f"resultado_{ticker}_{date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
