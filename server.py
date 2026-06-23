import os
import time
import threading
from datetime import datetime
import requests
from fastapi import FastAPI, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import yfinance as yf

# ==========================================
# 1. CONFIGURAÇÕES INICIAIS E AMBIENTE
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_b3.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "alertab3@avisapramim.com.br")

app = FastAPI(title="Avisa Pra Mim - Radar B3")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ==========================================
# 2. MODELO DO BANCO DE DADOS
# ==========================================

class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    ativo = Column(String, index=True, nullable=False)
    preco_alvo = Column(Float, nullable=False)
    condicao = Column(String, nullable=False)
    ativo_sistema = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. FUNÇÕES DE ENVIO DE E-MAIL (RESEND API)
# ==========================================

def enviar_email_via_resend(destino, assunto, corpo_texto):
    if not RESEND_API_KEY:
        print("⚠️ Erro: RESEND_API_KEY não configurada no ambiente.")
        return

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": f"Avisa Pra Mim <{EMAIL_REMETENTE}>",
        "to": [destino],
        "subject": assunto,
        "text": corpo_texto
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in [200, 201]:
            print(f"📧 E-mail enviado com sucesso via Resend para {destino}!")
        else:
            print(f"❌ Falha ao enviar e-mail pelo Resend: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"💥 Erro na conexão com a API do Resend: {e}")

def enviar_email_confirmacao(destino, ativo, preco_atual, preco_alvo, condicao):
    texto_condicao = "MAIOR ou igual a" if condicao == "maior" else "MENOR ou igual a"
    corpo = (
        f"✅ MONITORAMENTO CONFIGURADO COM SUCESSO!\n\n"
        f"Seu robô para o ativo {ativo} está ativo.\n\n"
        f"📊 Cotação Atual de Mercado: R$ {preco_atual:.2f}\n"
        f"🎯 Seu Preço Alvo: R$ {preco_alvo:.2f}\n"
        f"⚙️ Regra de Disparo: Avisar quando o preço ficar {texto_condicao} R$ {preco_alvo:.2f}\n\n"
        f"O Avisa Pra Mim enviará uma mensagem assim que este objetivo for atingido!"
    )
    enviar_email_via_resend(destino, f"📡 Avisa Pra Mim: Monitoramento de {ativo} Ativado!", corpo)

def enviar_email_b3(destino, ativo, preco_alvo, preco_atual, condicao):
    acao_sugerida = "🚨 HORA DE VENDER (Preço Alto)" if condicao == "maior" else "🟢 OPORTUNIDADE DE COMPRA (Preço Baixo)"
    corpo = (
        f"🚨 ALVO ATINGIDO!\n\n"
        f"O ativo {ativo} atingiu o objetivo configurado.\n\n"
        f"📌 Situação: {acao_sugerida}\n"
        f"Preço Alvo Configurado: R$ {preco_alvo:.2f}\n"
        f"Preço Atual de Mercado: R$ {preco_atual:.2f}\n\n"
        f"Este monitoramento foi encerrado e removido do radar dinâmico."
    )
    enviar_email_via_resend(destino, f"🔔 Avisa Pra Mim: {ativo} atingiu R$ {preco_atual:.2f}!", corpo)

# ==========================================
# 4. ROTAS DO FASTAPI (INTERFACE INTERNA)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    # 💡 Cole o conteúdo completo do seu 'index.html' original dentro das três aspas abaixo 
    # para que a sua interface fique 100% idêntica ao design que você criou!
    html_content = """
 <!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Radar B3 - Inteligente</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center justify-center font-sans p-4">

    <div class="max-w-xl w-full bg-slate-900 p-8 rounded-2xl shadow-2xl border border-slate-800">
        <div class="text-center mb-6">
            <h1 class="text-3xl font-extrabold text-green-400">📡 Radar B3</h1>
            <p class="text-slate-400 mt-2 text-sm">Automação inteligente e sugestão de operação em tempo real.</p>
        </div>

        <form id="formB3" class="space-y-4">
            <div>
                <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Código do Ativo (ex: PETR4, VALE3)</label>
                <div class="relative">
                    <input type="text" id="ativo" placeholder="Digite e clique fora..." required
                        class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500 uppercase">
                    <span id="precoTempoReal" class="absolute right-3 top-3 text-xs font-bold text-slate-500 hidden"></span>
                </div>
            </div>

            <div>
                <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Seu E-mail para Alerta</label>
                <input type="email" id="email" placeholder="seuemail@exemplo.com" required
                    class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Preço Alvo Desejado (R$)</label>
                    <input type="number" step="0.01" id="preco" placeholder="0.00" required
                        class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Me avise quando for:</label>
                    <select id="condicao" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-green-500">
                        <option value="maior">📈 Maior ou Igual</option>
                        <option value="menor">📉 Menor ou Igual</option>
                    </select>
                </div>
            </div>

            <button type="submit" class="w-full bg-green-500 hover:bg-green-600 text-slate-950 font-bold py-3 px-4 rounded-lg transition duration-200 shadow-lg">
                Ativar Radar B3 🚀
            </button>
        </form>

        <div id="feedback" class="mt-6 hidden p-5 rounded-xl border"></div>
    </div>

    <script>
        const inputAtivo = document.getElementById('ativo');
        const inputPreco = document.getElementById('preco');
        const selectCondicao = document.getElementById('condicao');
        const precoTempoReal = document.getElementById('precoTempoReal');
        const feedback = document.getElementById('feedback');

        let valorCotacaoAtual = 0; // Guarda o valor para comparar depois

        // AJUSTE 1: Exibe o preço com a legenda "Cotação Atual:" ao perder o foco
        inputAtivo.addEventListener('blur', async () => {
            const ativoVal = inputAtivo.value.trim();
            if (!ativoVal) return;

            precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-blue-400 animate-pulse";
            precoTempoReal.innerText = "Buscando...";
            precoTempoReal.classList.remove('hidden');

            try {
                const response = await fetch(`https://radar-b3.onrender.com/api/preco/${ativoVal}`);
                const dados = await response.json();

                if (dados.status === "sucesso") {
                    valorCotacaoAtual = dados.preco_atual;
                    precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-green-400";
                    precoTempoReal.innerText = `Cotação Atual: R$ ${valorCotacaoAtual.toFixed(2)}`;
                } else {
                    precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-red-500";
                    precoTempoReal.innerText = "Não encontrado";
                    valorCotacaoAtual = 0;
                }
            } catch (err) {
                precoTempoReal.className = "absolute right-3 top-3 text-xs font-bold text-red-500";
                precoTempoReal.innerText = "Erro de conexão";
                valorCotacaoAtual = 0;
            }
        });

        // AJUSTE 2: Sugere automaticamente a condição conforme o usuário digita o preço
        inputPreco.addEventListener('input', () => {
            if (valorCotacaoAtual === 0) return; // Se não buscou o ativo ainda, não faz nada
            
            const valorDigitado = parseFloat(inputPreco.value);
            if (isNaN(valorDigitado)) return;

            if (valorDigitado > valorCotacaoAtual) {
                selectCondicao.value = "maior"; // Se o alvo for maior que a cotação -> Maior ou Igual
            } else {
                selectCondicao.value = "menor"; // Se o alvo for menor que a cotação -> Menor ou Igual
            }
        });

        // AJUSTE 3: Exibe os dados pré-cadastrados com sucesso e para qual e-mail foi enviado
        document.getElementById('formB3').addEventListener('submit', async (e) => {
            e.preventDefault();
            const URL_API = 'https://radar-b3.onrender.com/api/alerta/';

            feedback.className = "mt-6 p-5 rounded-xl border bg-blue-950/40 text-blue-300 border-blue-800 text-center text-sm font-medium";
            feedback.innerText = "Registrando o seu alerta de monitoramento...";
            feedback.classList.remove('hidden');

            try {
                const response = await fetch(URL_API, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({
                        'email': document.getElementById('email').value,
                        'ativo': inputAtivo.value,
                        'preco_alvo': inputPreco.value,
                        'condicao': selectCondicao.value
                    })
                });

                const dados = await response.json();

                if (dados.status === "sucesso") {
                    const textoRegra = dados.condicao === "maior" ? "📈 MAIOR OU IGUAL" : "📉 MENOR OU IGUAL";
                    const corRegra = dados.condicao === "maior" ? "bg-red-500/20 text-red-400 border-red-500/30" : "bg-green-500/20 text-green-400 border-green-500/30";

                    feedback.className = "mt-6 p-5 rounded-xl border bg-slate-950 border-slate-800 text-left space-y-3 shadow-inner border-green-900/50";
                    
                    // Estrutura visual do Ajuste 3
                    feedback.innerHTML = `
                        <div class="border-b border-slate-800 pb-2">
                            <span class="text-base font-bold text-green-400 block">🎉 PRÉ-CADASTRO REALIZADO COM SUCESSO!</span>
                            <span class="text-xs text-slate-400">O robô já iniciou o monitoramento de mercado.</span>
                        </div>
                        <div class="space-y-1 text-sm mt-2">
                            <p class="text-white">• <b>Ativo cadastrado:</b> ${dados.ativo}</p>
                            <p class="text-white">• <b>Cotação de referência:</b> R$ ${dados.preco_atual.toFixed(2)}</p>
                            <p class="text-white">• <b>Seu Preço Alvo:</b> R$ ${dados.preco_alvo.toFixed(2)}</p>
                            <p class="text-white">• <b>Condição de disparo:</b> <span class="text-xs px-2 py-0.5 rounded font-bold ${corRegra}">${textoRegra}</span></p>
                        </div>
                        <div class="pt-3 border-t border-slate-800 text-xs text-emerald-400 font-medium flex items-center gap-1">
                            📧 Um e-mail de confirmação foi enviado para: <span class="text-white underline">${dados.email}</span>
                        </div>
                    `;
                    document.getElementById('formB3').reset();
                    precoTempoReal.classList.add('hidden');
                    valorCotacaoAtual = 0;
                } else {
                    feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                    feedback.innerText = dados.mensagem;
                }
            } catch (err) {
                feedback.className = "mt-6 p-5 rounded-xl border bg-red-900/40 text-red-300 border-red-800 text-center text-sm font-medium";
                feedback.innerText = "Erro ao conectar com o servidor.";
            }
        });
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/preco/{ativo}")
def obter_preco_ativo(ativo: str):
    """
    Rota que atende à busca em tempo real feita pelo Javascript do seu index.html!
    """
    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker

    try:
        dados_acao = yf.Ticker(ticker_yahoo)
        
        # 1ª tentativa: Tempo real via Info
        preco_atual = dados_acao.info.get("regularMarketPrice")
        
        # 2ª tentativa: Histórico diário recente (caso pregão fechado)
        if not preco_atual:
            historico = dados_acao.history(period="1d")
            if not historico.empty:
                preco_atual = historico["Close"].iloc[-1]
            else:
                # 3ª tentativa: Margem de segurança de 5 dias
                preco_atual = dados_acao.history(period="5d")["Close"].iloc[-1]
                
        return {"ativo": ticker.replace(".SA", ""), "preco": round(preco_atual, 2)}
        
    except Exception as e:
        print(f"Erro na rota API de cotação para {ativo}: {e}")
        raise HTTPException(status_code=404, detail="Ativo não encontrado ou erro na busca")

@app.post("/configurar-alerta")
def configurar_alerta(
    email: str = Form(...),
    ativo: str = Form(...),
    preco_alvo: float = Form(...),
    condicao: str = Form(...),
    db: Session = Depends(get_db)
):
    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker
        ticker = ticker.replace(".SA", "")

    try:
        dados_acao = yf.Ticker(ticker_yahoo)
        preco_atual = dados_acao.info.get("regularMarketPrice")
        if not preco_atual:
            historico = dados_acao.history(period="1d")
            if not historico.empty:
                preco_atual = historico["Close"].iloc[-1]
            else:
                preco_atual = dados_acao.history(period="5d")["Close"].iloc[-1]
                
    except Exception as e:
        print(f"Erro ao buscar cotação de {ticker}: {e}")
        raise HTTPException(status_code=400, detail=f"Não foi possível encontrar a cotação para o ativo {ticker}.")

    novo_alerta = Alerta(
        email=email.strip().lower(),
        ativo=ticker,
        preco_alvo=preco_alvo,
        condicao=condicao,
        ativo_sistema=True
    )
    db.add(novo_alerta)
    db.commit()

    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)

    return RedirectResponse(url="/", status_code=303)

# ==========================================
# 5. LOOP DE MONITORAMENTO EM SEGUNDO PLANO
# ==========================================

def loop_monitoramento_b3():
    print("🤖 Robô de monitoramento de ativos B3 iniciado com sucesso!")
    while True:
        db = SessionLocal()
        try:
            alertas_ativos = db.query(Alerta).filter(Alerta.ativo_sistema == True).all()

            if alertas_ativos:
                print(f"📊 Verificando {len(alertas_ativos)} monitoramentos no radar...")
                
                ativos_unicos = list(set([a.ativo for a in alertas_ativos]))
                cotacoes = {}

                for ativo in ativos_unicos:
                    try:
                        ticker_sa = f"{ativo}.SA"
                        dados = yf.Ticker(ticker_sa)
                        
                        preco = dados.info.get("regularMarketPrice")
                        if not preco:
                            hist = dados.history(period="1d")
                            preco = hist["Close"].iloc[-1] if not hist.empty else dados.history(period="5d")["Close"].iloc[-1]
                        
                        cotacoes[ativo] = preco
                    except Exception as e:
                        print(f"⚠️ Erro ao buscar cotação de {ativo} no loop: {e}")

                for alerta in alertas_ativos:
                    preco_atual = cotacoes.get(alerta.ativo)
                    if preco_atual is None:
                        continue

                    disparar = False
                    if alerta.condicao == "maior" and preco_atual >= alerta.preco_alvo:
                        disparar = True
                    elif alerta.condicao == "menor" and preco_atual <= alerta.preco_alvo:
                        disparar = True

                    if disparar:
                        print(f"🚨 ALVO ATINGIDO: {alerta.ativo} chegou a R$ {preco_atual:.2f}")
                        enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                        
                        alerta.ativo_sistema = False
                        db.commit()

        except Exception as e:
            print(f"💥 Erro crítico no loop do monitor: {e}")
        finally:
            db.close()

        time.sleep(300)

thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
thread_robo.start()