import re

# ==========================================
# Turkish Categorical Keyword Optimization (Elite Version)
# ==========================================

SPORTS_KEYWORDS = {
    "high": [
        "beşiktaş", "fenerbahçe", "galatasaray", "trabzonspor", "samsunspor", "başakşehir", "manisa fk", "süper lig", "bandırmaspor", "adana demirspor", "göztepe", "kasımpaşa", "alanyaspor", "antalyaspor",
        "manchester city", "arsenal", "liverpool", "manchester united", "chelsea", "tottenham", "aston villa",
        "real madrid", "barcelona", "atletico madrid", "villarreal", "real sociedad", "girona",
        "inter milan", "ac milan", "juventus", "napoli", "as roma", "atalanta", "lazio",
        "bayern münih", "borussia dortmund", "bayer leverkusen", "rb leipzig", "stuttgart",
        "psg", "paris saint-germain", "marsilya", "lyon", "monaco", "lille",
        "vinicius", "mbappe", "haaland", "bellingham", "rodri", "de bruyne", "mohamed salah",
        "harry kane", "lewandowski", "bukayo saka", "phil foden", "florian wirtz", "musiala",
        "lautaro martinez", "messi", "ronaldo", "luka modric", "neymar", "benzema", "yamal",
        "griezmann", "bernardo silva", "kimmich", "valverde", "courtois", "alisson", "van dijk",
        "arda güler", "kenan yıldız", "hakan çalhanoğlu", "barış alper yılmaz", "icardi",
        "dzeko", "osimhen", "tadic", "fred", "rafa silva", "en-nesyri",
        "futbol", "süper lig", "şampiyonlar ligi", "uefa", "avrupa ligi", "konferans ligi",
        "derbi", "transfer", "teknik direktör", "euroleague", "nba", "voleybol", "tenis"
    ],
    "medium": ["penaltı", "gol", "maç", "skor", "kupa", "madalya", "stadyum", "idman", "fikstür"],
    "low": ["takım", "oyuncu", "hakem", "antrenman"]
}

ECONOMY_KEYWORDS = {
    "high": [
        "tcmb", "merkez bankası", "fed", "ecb", "faiz kararı", "enflasyon", "tüfe", "üfe",
        "borsa istanbul", "bist 100", "nasdaq", "dow jones", "s&p 500", "wall street", "nikkei", "dax",
        "dolar/tl", "euro/tl", "döviz kuru", "altın fiyatları", "gram altın", "çeyrek altın", "ons altın",
        "kripto para", "bitcoin", "btc", "ethereum", "eth", "binance", "blockchain", "altcoin",
        "halka arz", "temettü", "kap bildirimi", "asgari ücret", "emekli zammı", "vergi paketi",
        "cari açık", "gsyh", "büyüme rakamları", "resesyon", "stagflasyon", "konkordato",
        "moody's", "fitch", "s&p", "msci", "jpmorgan", "goldman sachs"
    ],
    "medium": ["ihracat", "ithalat", "mevduat", "swap", "kredi notu", "bütçe açığı", "alım gücü", "maliyet", "tüketici"],
    "low": ["fiyat", "artış", "borç", "şirket", "piyasa", "kar", "zarar", "zam"]
}

TECHNOLOGY_KEYWORDS = {
    "high": [
        "yapay zeka", "ai", "openai", "chatgpt", "gemini", "claude", "deepfake", "makine öğrenmesi",
        "nvidia", "apple", "iphone", "google", "alphabet", "microsoft", "microsoft azure", "tesla",
        "meta", "instagram", "facebook", "whatsapp", "amazon", "netflix", "spacex", "starlink",
        "baykar", "tusaş", "aselsan", "savunma sanayii", "togg", "iha", "siha", "kaaan", "hürjet",
        "yarı iletken", "çip krizi", "işlemci", "intel", "amd", "tsmc", "5g", "6g", "fiber internet",
        "siber güvenlik", "siber saldırı", "hacker", "veri sızıntısı", "kuantum", "blockchain",
        "uzay", "nasa", "roket", "fırlatma", "uydu", "mavi orijin"
    ],
    "medium": ["yazılım", "donanım", "bulut bilişim", "cloud", "robot", "drone", "uygulama", "android", "ios", "windows"],
    "low": ["dijital", "internet", "platform", "akıllı telefon", "otonom", "güncelleme", "şifre"]
}

POLITICS_KEYWORDS = {
    "high": ["cumhurbaşkanı", "erdoğan", "özgür özel", "bahçeli", "imamoğlu", "mansur yavaş", "ak parti", "chp", "mhp", "dem partisi", "iyi parti", "zafer partisi", "tbmm", "meclis", "kabine", "ysk", "anayasa", "beyaz saray", "kremlin", "pentagon"],
    "medium": ["seçim anketi", "erken seçim", "ittifak", "yasa", "kanun", "zirve", "diplomasi", "nato", "bm", "birleşmiş milletler", "istifa", "gözaltı", "tutuklama", "önerge", "koalisyon", "gensoru", "torba yasa"],
    "low": ["açıklama", "toplantı", "karar", "bakanlık", "lider", "tepki", "eleştiri", "ziyaret", "gündem"]
}

ART_KEYWORDS = {
    "high": ["sinema", "film", "dizi", "konser", "festival", "sergi", "kitap", "yazar", "oyuncu", "oyuncuları", "başrol", "televizyon", "ekranlarda", "vizyon", "albüm", "tarkan", "sezen aksu", "cem yılmaz", "oscar", "altın portakal", "cannes", "bienal", "netflix", "disney+", "bluetv"],
    "medium": ["gala", "sahne", "yönetmen", "fragman", "reyting", "aşk", "ayrılık", "boşanma", "evlilik", "fenomen", "influencer", "gişe rekoru", "tiyatro", "prömiyer", "senaryo", "yapımcı", "karakter", "izleyici", "beyaz perde"],
    "low": ["izle", "dinle", "eğlence", "magazin", "ünlü", "moda", "tarz", "viral", "ödül töreni"]
}

GUNDEM_KEYWORDS = {
    "high": ["deprem", "yangın", "sel", "cinayet", "kaza", "patlama", "afad", "polis", "jandarma", "meteoroloji", "şiddetli fırtına", "son dakika", "flaş haber", "acil durum"],
    "medium": ["vefat", "kayıp", "arama kurtarma", "trafik kazası", "gözaltı", "adliye", "asayiş", "uyarı", "sağanak", "ağır ceza", "müebbet", "dolandırıcılık", "gasp", "hırsızlık"],
    "low": ["haber", "olay", "hava durumu", "sıcaklık", "belediye", "valilik", "hizmet", "duyuru", "trafik yoğunluğu"]
}

CAT_MAP = {
    "Spor": SPORTS_KEYWORDS,
    "Ekonomi": ECONOMY_KEYWORDS,
    "Teknoloji": TECHNOLOGY_KEYWORDS,
    "Siyaset": POLITICS_KEYWORDS,
    "Sanat": ART_KEYWORDS,
    "Gündem": GUNDEM_KEYWORDS
}

NEGATIVE_KEYWORDS = {
    "political_vs_sports": {
        "dominant_category": "Spor",
        "keywords": ["galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor", "süper lig", "maç", "gol", "transfer"],
        "penalty": -100, "affects": ["Siyaset"]
    },
    "political_vs_accident": {
        "dominant_category": "Gündem",
        "keywords": ["deprem", "yangın", "sel", "kaza", "can kaybı", "patlama"],
        "penalty": -80, "affects": ["Siyaset", "Ekonomi"]
    },
    "politics_exclusive": {
        "dominant_category": "Siyaset", 
        "keywords": ["resmi gazete", "kararname", "kanun teklifi", "tbmm", "anayasa mahkemesi", "genel kurul", "grup toplantısı"],
        "penalty": -50, "affects": ["Spor", "Sanat", "Teknoloji", "Gündem"],
        "soft_penalty": -20, "soft_affects": ["Ekonomi"] 
    },
    "economy_exclusive": {
        "dominant_category": "Ekonomi",
        "keywords": ["borsa istanbul", "bist 100", "döviz kuru", "faiz kararı", "enflasyon rakamları", "temettü", "kap bildirimi"],
        "penalty": -40, "affects": ["Spor", "Sanat", "Teknoloji"],
        "soft_penalty": -15, "soft_affects": ["Siyaset", "Gündem"]
    }
}

def normalize_text(text: str) -> str:
    """Strict Turkish character normalization for consistent matching"""
    if not text: return ""
    text = text.replace('İ', 'i').replace('I', 'ı').replace('Ğ', 'ğ').replace('Ü', 'ü').replace('Ş', 'ş').replace('Ö', 'ö').replace('Ç', 'ç')
    return text.lower()

def calculate_keyword_score(text: str, keywords_dict: dict) -> int:
    """Calculates weighting for a specific category based on text frequency"""
    score = 0
    for word in keywords_dict["high"]:
        if word in text: score += 60 
    for word in keywords_dict["medium"]:
        if word in text: score += 20
    for word in keywords_dict["low"]:
        if word in text: score += 5
    return score

def apply_negative_logic(scores: dict, text: str) -> dict:
    """Applies cross-categorical penalties to avoid classification bias"""
    for rule_name, config in NEGATIVE_KEYWORDS.items():
        found = any(word in text for word in config["keywords"])
        if found:
            for target in config["affects"]:
                if target in scores: scores[target] = max(0, scores[target] + config["penalty"])
            if "soft_affects" in config:
                for target in config["soft_affects"]:
                    if target in scores: scores[target] = max(0, scores[target] + config["soft_penalty"])
    return scores

def fast_classify(text: str) -> str:
    """
    Layer 0: Instant categorization based on keyword density.
    Used by collectors to avoid the "Gündem Lag".
    """
    text_norm = normalize_text(text)
    
    scores = {}
    for cat_name, keywords in CAT_MAP.items():
        scores[cat_name] = calculate_keyword_score(text_norm, keywords)

    # Logic: Pick highest score, must beat Gündem by 1.8x if not Gündem
    top_cat = max(scores, key=scores.get)
    if top_cat != "Gündem":
        if scores[top_cat] < (1.8 * scores["Gündem"]):
            return "Gündem"
        # Minimum matches rule
        high_matches = sum(1 for w in CAT_MAP[top_cat]["high"] if w in text_norm)
        if high_matches < 1: # Instant check is less strict than AI check
            return "Gündem"
            
    return top_cat

def decide_final_category(ai_category: str, text: str) -> tuple:
    """
    Layer 4: Density-Based Categorization & Safety Guard.
    Calculates Keyword Density (Score / Word Count) to prevent noise-based misclassification.
    """
    text_norm = normalize_text(text)
    words = text_norm.split()
    word_count = len(words) if len(words) > 0 else 1

    scores = {}
    match_counts = {}
    for cat_name, keywords in CAT_MAP.items():
        score = calculate_keyword_score(text_norm, keywords)
        
        matches = 0
        for word in keywords["high"]:
            if word in text_norm: matches += 1
        for word in keywords["medium"]:
            if word in text_norm: matches += 1
        for word in keywords["low"]:
            if word in text_norm: matches += 1
            
        scores[cat_name] = score
        match_counts[cat_name] = matches

    scores = apply_negative_logic(scores, text_norm)

    densities = {k: v / word_count for k, v in scores.items()}
    top_cat = max(densities, key=densities.get)
    
    if top_cat != "Gündem":
        if match_counts[top_cat] < 2: 
            top_cat = "Gündem"
        elif densities[top_cat] < (1.8 * densities["Gündem"]): 
            top_cat = "Gündem"

    if ai_category == top_cat: return top_cat, False
    
    # If AI disagrees, check if AI's choice has reasonable density
    # Thresholds adjusted for density (score/word_count)
    if densities.get(top_cat, 0) > 0.8 and match_counts[top_cat] >= 3:
        return top_cat, True
    
    return ai_category, False
