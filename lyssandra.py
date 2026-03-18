import ollama
import edge_tts
import asyncio
import subprocess
import re
import os

# Configurações
VOZ = "pt-BR-FranciscaNeural" 
RATE = "+10%"
PITCH = "-5Hz"
MODELO_OLLAMA = "lyssandra2:latest"

# Caminho da pasta de cache
CACHE_DIR = os.path.join("cache", "output")

# Cria a pasta se ela não existir
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

def limpar_texto_para_audio(texto):
    """Remove ações e símbolos para o áudio não bugar"""
    t = re.sub(r'\[.*?\]|\(.*?\)', '', texto)
    t = re.sub(r'\*[^*]+\s[^*]+\*', '', t)
    t = t.replace('*', '').replace('"', '').strip()
    return t

async def baixar_audio(texto, nome_arquivo):
    """Tarefa dedicada apenas ao download dentro da pasta cache/output"""
    try:
        # Define o caminho completo dentro da pasta de cache
        caminho_completo = os.path.join(CACHE_DIR, nome_arquivo)
        comunicador = edge_tts.Communicate(texto, VOZ, rate=RATE, pitch=PITCH)           
        await comunicador.save(caminho_completo)
        return True
    except Exception as e:
        print(f"\n[Erro Download: {e}]")
        return False

async def processador_de_audio(fila_reproducao):
    """Lê a fila infinitamente e toca os arquivos na ordem"""
    while True:
        item = await fila_reproducao.get()
        if item is None: break 
        
        nome_arquivo = item['arquivo']
        tarefa_download = item['tarefa']
        
        caminho_completo = os.path.join(CACHE_DIR, nome_arquivo)
        
        # Espera o download terminar
        await tarefa_download
        
        if os.path.exists(caminho_completo):
            processo = await asyncio.create_subprocess_exec(
                "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", caminho_completo,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await processo.wait()
            
            # Pequena pausa para o sistema liberar o arquivo antes de deletar
            await asyncio.sleep(0.1)
            try:
                if os.path.exists(caminho_completo):
                    os.remove(caminho_completo)
            except:
                pass
        
        fila_reproducao.task_done()

async def get_input(prompt):
    return await asyncio.to_thread(input, prompt)

async def main_async():
    print("\n👑 LYSSANDRA ONLINE - SALVANDO EM CACHE/OUTPUT")
    
    historico_chat = []
    fila_reproducao = asyncio.Queue()
    tarefa_processador = asyncio.create_task(processador_de_audio(fila_reproducao))
    contador = 0

    while True:
        pergunta = await get_input("\nVocê: ")
        
        if pergunta.strip().lower() == 'sair': 
            await fila_reproducao.put(None)
            break
            
        historico_chat.append({'role': 'user', 'content': pergunta})
        print("Lyssandra: ", end="", flush=True)
        
        try:
            resposta_stream = ollama.chat(model=MODELO_OLLAMA, messages=historico_chat, stream=True)
            
            buffer_texto = ""
            resposta_completa_ia = ""

            for pedaco in resposta_stream:
                token = pedaco['message']['content']
                print(token, end="", flush=True)
                
                buffer_texto += token
                resposta_completa_ia += token
                
                # Gatilho de frase
                if any(p in buffer_texto for p in ['.', '!', '?', '\n']) and len(buffer_texto) > 15:
                    partes = re.split(r'(?<=[.!?\n])', buffer_texto, 1)
                    
                    if len(partes) > 1:
                        frase_pronta = partes[0].strip()
                        texto_para_voz = limpar_texto_para_audio(frase_pronta)
                        
                        if len(texto_para_voz) > 2:
                            nome_arq = f"fala_{contador}.mp3"
                            # DISPARA DOWNLOAD PARALELO
                            t_dl = asyncio.create_task(baixar_audio(texto_para_voz, nome_arq))
                            # JOGA NA FILA DE PLAY
                            fila_reproducao.put_nowait({'arquivo': nome_arq, 'tarefa': t_dl})
                            contador += 1
                            
                        buffer_texto = partes[1]

            # Processa a sobra final
            sobra = limpar_texto_para_audio(buffer_texto)
            if len(sobra) > 2:
                nome_arq = f"fala_{contador}.mp3"
                t_dl = asyncio.create_task(baixar_audio(sobra, nome_arq))
                fila_reproducao.put_nowait({'arquivo': nome_arq, 'tarefa': t_dl})
                contador += 1

            historico_chat.append({'role': 'assistant', 'content': resposta_completa_ia})
            print() 
            
        except Exception as e:
            print(f"\n❌ Erro: {e}")

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nSaindo...")

if __name__ == "__main__":
    main()