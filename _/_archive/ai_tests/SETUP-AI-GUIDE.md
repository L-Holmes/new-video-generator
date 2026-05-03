curl -fsSL https://ollama.com/install.sh | sh

ollama pull qwen2.5:7b


ollama run qwen2.5:7b "hello, are you working?"




pip install spacy
python -m spacy download en_core_web_sm
