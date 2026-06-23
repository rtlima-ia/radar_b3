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

# Dicionário global para guardar os preços na memória do servidor e evitar bloqueios
CACHE_COTCOES = {}
CACHE_EXPIRACAO_SEGUNDOS = 60  # Guarda o preço por 1 minuto antes de consultar a internet de novo

@app.get("/api/preco/{ativo}")
def obter_preco_ativo(ativo: str):
    """
    Rota de API ultra-resistente a bloqueios. Usa cache interno e faz 
    requisição HTTP direta à API de sumário do Yahoo Finance para evitar Rate Limits.
    """
    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker
        
    nome_ativo = ticker.replace(".SA", "")
    tempo_atual = time.time()

    # 1. Verifica se já temos o preço desse ativo no cache e se ele ainda é recente
    if nome_ativo in CACHE_COTCOES:
        dados_cache = CACHE_COTCOES[nome_ativo]
        if tempo_atual - dados_cache["timestamp"] < CACHE_EXPIRACAO_SEGUNDOS:
            print(f"⚡ [CACHE] Preço de {nome_ativo} retornado da memória interna.")
            return {"ativo": nome_ativo, "preco": dados_cache["preco"]}

    # 2. Se não estiver no cache ou expirou, faz consulta direta via HTTP (MUITO mais leve que a biblioteca yf)
    try:
        # URL da API interna e pública do Yahoo Finance para cotações em tempo real
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_yahoo}"
        
        # Fingimos ser um navegador real para o Yahoo não bloquear a requisição
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        resposta = requests.get(url, headers=headers, timeout=10)
        
        if resposta.status_code == 200:
            dados = resposta.json()
            # Extrai o preço de fechamento/atual de dentro do JSON retornado pelo Yahoo
            meta = dados.get("chart", {}).get("result", [{}])[0].get("meta", {})
            preco_atual = meta.get("regularMarketPrice")
            
# ... (código anterior da rota)
        if preco_atual is not None:
            preco_final = round(float(preco_atual), 2)
            
            # Salva no cache
            CACHE_COTCOES[nome_ativo] = {
                "preco": preco_final,
                "timestamp": tempo_atual
            }
            print(f"🌍 [API YAHOO] Cotação de {nome_ativo} atualizada: R$ {preco_final}")
            
            # 🔥 RETORNO BLINDADO: Devolve em todos os formatos que o seu HTML possa pedir!
            return {
                "ativo": nome_ativo,
                "preco": preco_final,
                "price": preco_final,        # Caso seu HTML espere em inglês
                "valor": preco_final,        # Caso seu HTML espere 'valor'
                "PRECO": preco_final,        # Caso seu HTML espere em maiúsculo
                "PRICE": preco_final         # Caso seu HTML espere 'PRICE'
            }
        # Se a API direta falhar, tenta usar a biblioteca yfinance tradicional como última alternativa
        print("⚠️ API Direta falhou ou retornou vazio. Tentando fallback via biblioteca yfinance...")
        dados_acao = yf.Ticker(ticker_yahoo)
        preco_atual = dados_acao.history(period="1d")["Close"].iloc[-1]
        
        preco_final = round(float(preco_atual), 2)
        CACHE_COTCOES[nome_ativo] = {"preco": preco_final, "timestamp": tempo_atual}
        return {"ativo": nome_ativo, "preco": preco_final}
        
    except Exception as e:
        print(f"💥 Erro total na rota de cotação para {ativo}: {e}")
        
        # 3. SISTEMA DE SEGURANÇA MÁXIMA: Se o Yahoo bloquear TOTALMENTE, mas nós tivermos
        # qualquer preço histórico guardado em cache (mesmo antigo), entregamos ele!
        # Isso impede que o seu site dê erro 404 na tela do cliente.
# ... (dentro do except, na linha do emergência do cache)
        if nome_ativo in CACHE_COTCOES:
            preco_antigo = CACHE_COTCOES[nome_ativo]["preco"]
            print(f"🛟 [EMERGÊNCIA] Entregando último preço em cache para {nome_ativo}")
            return {
                "ativo": nome_ativo,
                "preco": preco_antigo,
                "price": preco_antigo,
                "valor": preco_antigo,
                "PRECO": preco_antigo,
                "PRICE": preco_antigo
            }
            
        raise HTTPException(
            status_code=404, 
            detail="Serviço de cotações temporariamente indisponível. Tente novamente em alguns minutos."
        )

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