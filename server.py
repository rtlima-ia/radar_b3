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

# URL do Banco de Dados (PostgreSQL no Render ou SQLite local para testes)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./radar_b3.db")

# Ajuste para compatibilidade do SQLAlchemy com conexões antigas do Heroku/Render (postgres:// vs postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# CONFIGURAÇÃO DO RESEND (Via Variáveis de Ambiente do Render)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "alertab3@avisapramim.com.br")

app = FastAPI(title="Avisa Pra Mim - Radar B3")

# Monta a pasta de arquivos estáticos (CSS, imagens), se houver
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ==========================================
# 2. MODELO DO BANCO DE DADOS (Tabela Alertas)
# ==========================================

class Alerta(Base):
    __tablename__ = "alertas"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    ativo = Column(String, index=True, nullable=False)
    preco_alvo = Column(Float, nullable=False)
    condicao = Column(String, nullable=False)  # "maior" ou "menor"
    ativo_sistema = Column(Boolean, default=True)  # True = Monitorando, False = Já disparado

# Cria as tabelas se elas não existirem
Base.metadata.create_all(bind=engine)

# Dependência para abrir/fechar a sessão do banco em cada requisição
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
        print("⚠️ Erro: RESEND_API_KEY não configurada no ambiente do servidor.")
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
# 4. ROTAS DO FASTAPI (INTERFACE WEB)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    # Retorna o formulário HTML básico do seu app
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>Avisa Pra Mim - Radar B3</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; line-height: 1.6; }
            .form-group { margin-bottom: 15px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input, select { width: 100%; padding: 8px; box-sizing: border-box; }
            button { background-color: #007bff; color: white; border: none; padding: 10px 15px; cursor: pointer; width: 100%; font-size: 16px; }
            button:hover { background-color: #0056b3; }
        </style>
    </head>
    <body>
        <h2>📡 Avisa Pra Mim — Configurar Alerta B3</h2>
        <form action="/configurar-alerta" method="post">
            <div class="form-group">
                <label for="email">Seu E-mail:</label>
                <input type="email" id="email" name="email" placeholder="exemplo@gmail.com" required>
            </div>
            <div class="form-group">
                <label for="ativo">Código da Ação ou FII (ex: PETR4, VALE3):</label>
                <input type="text" id="ativo" name="ativo" placeholder="PETR4" required>
            </div>
            <div class="form-group">
                <label for="preco_alvo">Preço Alvo (R$):</label>
                <input type="number" step="0.01" id="preco_alvo" name="preco_alvo" placeholder="35.50" required>
            </div>
            <div class="form-group">
                <label for="condicao">Me avise quando o preço for:</label>
                <select id="condicao" name="condicao">
                    <option value="maior">Maior ou igual que o Alvo (Venda)</option>
                    <option value="menor">Menor ou igual que o Alvo (Compra)</option>
                </select>
            </div>
            <button type="submit">Ativar Monitoramento</button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/configurar-alerta")
def configurar_alerta(
    email: str = Form(...),
    ativo: str = Form(...),
    preco_alvo: float = Form(...),
    condicao: str = Form(...),
    db: Session = Depends(get_db)
):
    # Trata a entrada do ativo (garante letras maiúsculas e remove espaços)
    ticker = ativo.strip().upper()
    if not ticker.endswith(".SA"):
        ticker_yahoo = f"{ticker}.SA"
    else:
        ticker_yahoo = ticker
        ticker = ticker.replace(".SA", "")

    # Valida e busca o preço atual usando o yfinance
    try:
        dados_acao = yf.Ticker(ticker_yahoo)
        preco_atual = dados_acao.history(period="1d")["Close"].iloc[-1]
    except Exception:
        raise HTTPException(status_code=400, detail=f"Não foi possível encontrar a cotação para o ativo {ticker}.")

    # Salva o novo registro de monitoramento no banco de dados
    novo_alerta = Alerta(
        email=email.strip().lower(),
        ativo=ticker,
        preco_alvo=preco_alvo,
        condicao=condicao,
        ativo_sistema=True
    )
    db.add(novo_alerta)
    db.commit()

    # Dispara e-mail de confirmação usando a estrutura do Resend
    enviar_email_confirmacao(novo_alerta.email, novo_alerta.ativo, preco_atual, preco_alvo, condicao)

    # Redireciona de volta para a página inicial (pode ser ajustado para uma página de sucesso)
    return RedirectResponse(url="/", status_code=303)

# ==========================================
# 5. LOOP DE MONITORAMENTO EM SEGUNDO PLANO
# ==========================================

def loop_monitoramento_b3():
    print("🤖 Robô de monitoramento de ativos B3 iniciado com sucesso!")
    while True:
        db = SessionLocal()
        try:
            # Busca apenas os alertas que estão marcados como ativos no sistema
            alertas_ativos = db.query(Alerta).filter(Alerta.ativo_sistema == True).all()

            if alertas_ativos:
                print(f"📊 [{datetime.now().strftime('%H:%M:%S')}] Verificando {len(alertas_ativos)} monitoramentos no radar...")
                
                # Agrupa os ativos para evitar fazer requisições repetidas ao Yahoo Finance
                ativos_unicos = list(set([a.ativo for a in alertas_ativos]))
                cotacoes = {}

                for ativo in ativos_unicos:
                    try:
                        ticker_sa = f"{ativo}.SA"
                        dados = yf.Ticker(ticker_sa)
                        preco_atual = dados.history(period="1d")["Close"].iloc[-1]
                        cotacoes[ativo] = preco_atual
                    except Exception as e:
                        print(f"⚠️ Erro ao buscar cotação de {ativo}: {e}")

                # Avalia cada regra cadastrada pelos usuários
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
                        print(f"🚨 ALVO ATINGIDO: {alerta.ativo} chegou a R$ {preco_atual:.2f} (Alvo era R$ {alerta.preco_alvo:.2f})")
                        # Envia o alerta real usando o Resend
                        enviar_email_b3(alerta.email, alerta.ativo, alerta.preco_alvo, preco_atual, alerta.condicao)
                        
                        # Desativa o alerta no banco para não enviar repetidamente
                        alerta.ativo_sistema = False
                        db.commit()

        except Exception as e:
            print(f"💥 Erro crítico no loop do monitor: {e}")
        finally:
            db.close()

        # Intervalo entre checagens do robô (300 segundos = 5 minutos)
        time.sleep(300)

# Inicializa o robô de varredura em uma thread paralela para não travar a API Web
thread_robo = threading.Thread(target=loop_monitoramento_b3, daemon=True)
thread_robo.start()