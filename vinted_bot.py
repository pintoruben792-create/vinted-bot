import requests
import time

# Liste des recherches à surveiller
search_terms = [
    "nike running division",
    "nike phenom elite",
    "spodnie nike running",
    "aeroswift",
    "sweat nike tech",
    "spodnie under armour hybrid",
    "jacket under armour"
]

max_price = 150  # Prix maximum en PLN

# Boucle infinie pour surveiller les articles
while True:
    for search in search_terms:
        url = f"https://www.vinted.pl/api/v2/catalog/items?search_text={search}&order=newest_first"

        # User-Agent pour éviter le blocage par Vinted
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
        }

        # Requête vers Vinted
        response = requests.get(url, headers=headers)

        # Affiche le code de statut pour vérifier si Vinted répond
        print("Recherche :", search, "| Status code:", response.status_code)

        # Vérifie que la réponse est correcte avant de lire le JSON
        if response.status_code == 200:
            try:
                data = response.json()
                for item in data["items"]:
                    title = item["title"]
                    price = float(item["price"]["amount"])
                    link = item["url"]

                    if price <= max_price:
                        print("🔥 BON PLAN TROUVÉ")
                        print("Recherche :", search)
                        print("Article :", title)
                        print("Prix :", price, "PLN")
                        print(link)
                        print("--------------")
            except Exception as e:
                print("Erreur lecture JSON :", e)
        else:
            print("Attention : Vinted a bloqué la requête ou aucune donnée disponible.")

    # Pause de 60 secondes pour éviter d'être bloqué
    time.sleep(60)