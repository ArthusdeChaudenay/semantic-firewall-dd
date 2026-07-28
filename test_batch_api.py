import os
import requests

API_URL = "http://localhost:8000/webhook/ingest-file"
HF_DIR  = os.path.join("samples", "hf_test")

if not os.path.exists(HF_DIR):
    print(f"Erreur : le dossier {HF_DIR} n'existe pas. Lance d'abord : python test_sec.py")
    exit()

print("Pare-feu — Audit de masse sur les docs Hugging Face\n")
print(f"{'Fichier':<30} {'Verdict':<18} {'Type doc':<18} {'Action'}")
print("-" * 85)

ok = anomalie = erreur = 0

for filename in sorted(os.listdir(HF_DIR)):
    if not filename.endswith(".txt"):
        continue

    filepath = os.path.join(HF_DIR, filename)
    with open(filepath, "rb") as f:
        try:
            response = requests.post(
                API_URL,
                files={"file": (filename, f, "text/plain")},
                timeout=60,
            )
        except requests.exceptions.ConnectionError:
            print("Erreur : serveur FastAPI non accessible. Lance : .\\venv\\Scripts\\uvicorn.exe api:app --reload")
            break

    if response.status_code in (200, 422):
        res = response.json()
        statut   = res.get("statut_parefeu", "INCONNU")
        doc_type = res.get("doc_type", "?")
        message  = res.get("message", "")

        if statut == "CERTIFIÉ":
            action = "Ingestion OK"
            ok += 1
        elif statut == "ANOMALIE":
            action = "Alerte emise"
            anomalie += 1
        else:
            action = "Erreur analyse"
            erreur += 1

        print(f"{filename:<30} {statut:<18} {doc_type:<18} {action}")
        if res.get("anomalies"):
            for a in res["anomalies"][:2]:
                print(f"    └─ [{a.get('check','?')}] {a.get('message','')[:60]}")
    else:
        print(f"{filename:<30} HTTP {response.status_code}")
        erreur += 1

print("-" * 85)
print(f"Total : {ok} certifies | {anomalie} anomalies | {erreur} erreurs")
