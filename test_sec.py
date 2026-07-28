import os
from datasets import load_dataset

# 1. Définir le dossier de destination dans tes samples
OUTPUT_DIR = os.path.join("samples", "hf_test")

# Créer le dossier s'il n'existe pas encore
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("⏳ Chargement du dataset de recherche...")
try:
    dataset = load_dataset("zeroshot/twitter-financial-news-sentiment", split="train")
    print(f"✅ Dataset chargé. {len(dataset)} exemples disponibles.")
except Exception as e:
    print(f"❌ Erreur de chargement : {e}")
    exit()

print(f"\n📂 Sauvegarde des exemples dans {OUTPUT_DIR}...")
print("-" * 60)

# On extrait et on sauvegarde les 10 premiers exemples sous forme de fichiers .txt
for i in range(10):
    text_content = dataset[i]["text"]
    filename = f"hf_financial_doc_{i+1}.txt"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    # Écriture du fichier sur ton disque dur
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text_content)
        
    print(f"💾 Sauvegardé : {filename}")

print("-" * 60)
print(f"\n🏁 Terminé ! Tu as maintenant 10 vrais documents financiers de test dans ton dossier : {OUTPUT_DIR}")