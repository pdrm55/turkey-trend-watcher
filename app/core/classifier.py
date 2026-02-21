import re

# ==========================================
# Turkish Categorical Keyword Optimization (Elite Version 2.2)
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
    "medium": ["penaltı", "gol", "maç", "skor", "kupa", "madalya", "stadyum", "idman", "fikstür", "antrenman"],
    "low": ["takım", "oyuncu", "hakem", "sporcu", "kulüp"]
}

ECONOMY_KEYWORDS = {
    "high": [
        "tcmb", "merkez bankası", "fed", "ecb", "faiz kararı", "enflasyon", "tüfe", "üfe",
        "yatırım", "milyar lira", "milyon lira", "milyar tl", "milyon tl", "ihale", "proje maliyeti",
        "emekli", "ikramiye", "asgari ücret", "maaş zammı", "bayram ikramiyesi", "refah payı",
        "borsa istanbul", "bist 100", "nasdaq", "dow jones", "s&p 500", "wall street", "nikkei", "dax",
        "dolar/tl", "euro/tl", "döviz kuru", "altın fiyatları", "gram altın", "çeyrek altın", "ons altın",
        "kripto para", "bitcoin", "btc", "ethereum", "eth", "binance", "blockchain", "altcoin",
        "halka arz", "temettü", "kap bildirimi", "vergi paketi", "cari açık", "gsyh", "büyüme rakamları", 
        "resesyon", "stagflasyon", "konkordato", "moody's", "fitch", "s&p", "msci", "jpmorgan"
    ],
    "medium": ["ihracat", "ithalat", "mevduat", "swap", "kredi notu", "bütçe açığı", "alım gücü", "maliyet", "tüketici", "ekonomi", "finans"],
    "low": ["fiyat", "artış", "borç", "şirket", "piyasa", "kar", "zarar", "zam", "endeks"]
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
    "high": ["cumhurbaşkanı", "erdoğan", "özgür özel", "bahçeli", "imamoğlu", "mansur yavaş", "ak parti", "chp", "mhp", "dem partisi", "iyi parti", "zafer partisi", "tbmm", "meclis", "kabine", "ysk", "anayasa", "beyاز saray", "kremlin", "pentagon", "diplomasi", "dışişleri"],
    "medium": ["seçim anketi", "erken seçim", "ittifak", "yasa", "kanun", "zirve", "nato", "bm", "birleşmiş milletler", "istifa", "gözaltı", "tutuklama", "önerge", "koalisyon", "gensoru", "torba yasa", "belediye başkanı"],
    "low": ["açıklama", "toplantı", "karar", "bakanlık", "lider", "tepki", "eleştiri", "ziyaret", "gündem", "diplomatik"]
}

ART_KEYWORDS = {
    "high": ["sinema", "film", "dizi", "konser", "festival", "sergi", "kitap", "yazar", "oyuncu", "oyuncuları", "başrol", "televizyon", "ekranlarda", "vizyon", "albüm", "tarkan", "sezen aksu", "cem yılmaz", "oscar", "altın portakal", "cannes", "bienal", "netflix", "disney+", "bluetv", "sanatçı", "müzik", "şarkıcı", "sahne performansı", "kültür sanat", "edebiyat", "roman", "şiir", "ressam", "heykeltıraş", "müze", "galeri", "orkestra", "opera", "bale", "tiyatro oyunu", "stand-up", "belgesel", "kısa film", "altın küre", "grammy", "emmy", "tony ödülleri", "eurovision", "spotify", "youtube music", "apple music", "exxen", "gain", "amazon prime video", "mubi"],
    "medium": ["gala", "sahne", "yönetmen", "fragman", "reyting", "aşk", "ayrılık", "boşanma", "evlilik", "fenomen", "influencer", "gişe rekoru", "tiyatro", "prömiyer", "senaryo", "yapımcı", "karakter", "izleyici", "beyaz perde", "turne", "imza günü", "söyleşi", "eleştirmen", "küratör", "koleksiyoner", "müzayede", "beste", "güfte", "aranjman", "klip", "single", "plak", "kaset", "dijital platform", "yayın akışı", "sezon finali", "yeni sezon", "oyuncu kadrosu", "kırmızı halı", "magazin gündemi", "paparazzi", "ünlüler dünyası", "sosyal medya fenomeni", "youtuber", "tiktoker", "instagrammer", "podcast"],
    "low": ["izle", "dinle", "eğlence", "magazin", "ünlü", "moda", "tarz", "viral", "ödül töreni", "paylaşım", "takipçi", "beğeni", "yorum", "trend", "stil", "kombin", "makyaj", "estetik", "dedikodu", "skandal", "itiraf", "samimi açıklamalar"]
}

GUNDEM_KEYWORDS = {
    "high": [
        "deprem", "yangın", "sel", "cinayet", "kaza", "patlama", "afad", "polis", "jandarma", "meteoroloji", "şiddetli fırtına", "son dakika", "flaş haber", "acil durum", 
        "dsi", "vgm", "mgm", "toki", "karayolları", "meteoroloji", "belediyesi", "valiliği"
    ],
    "medium": [
        "vefat", "kayıp", "arama kurtarma", "trafik kazası", "gözaltı", "adliye", "asayiş", "uyarı", "sağanak", "ağır ceza", "müebbet", 
        "inşa etti", "hizmete açıldı", "baraj", "tesis", "altyapı", "üstyapı"
    ],
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
        "keywords": ["deprem", "yangın", "sel", "kaza", "can kaybı", "patlama", "hastane", "doktor"],
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
        "keywords": ["borsa istanbul", "bist 100", "döviz kuru", "faiz kararı", "enflasyon rakamları", "temettü", "kap bildirimi", "milyar lira", "emekli", "ikramiye"],
        "penalty": -60, "affects": ["Spor", "Sanat", "Teknoloji", "Gündem"]
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
    Layer 0: Instant categorization.
    Logic: Winner must have at least 60 points (1 High Match) AND beat Gündem by 1.8x.
    """
    text_norm = normalize_text(text)
    
    scores = {}
    for cat_name, keywords in CAT_MAP.items():
        scores[cat_name] = calculate_keyword_score(text_norm, keywords)

    if max(scores.values()) == 0:
        return "Gündem"

    top_cat = max(scores, key=scores.get)
    
    # --- Strict Winner Rules ---
    if top_cat != "Gündem":
        # Rule 1: A specific category must have at least ONE high match (60 pts) to trigger
        if scores[top_cat] < 60:
            return "Gündem"
            
        # Rule 2: Dominance check
        if scores[top_cat] < (1.8 * scores["Gündem"]):
            return "Gündem"
            
    return top_cat

def decide_final_category(ai_category: str, text: str) -> tuple:
    """
    Layer 4: Density-Based Categorization & Safety Guard.
    """
    text_norm = normalize_text(text)
    words = text_norm.split()
    word_count = len(words) if len(words) > 0 else 1

    scores = {}
    match_counts = {}
    for cat_name, keywords in CAT_MAP.items():
        score = calculate_keyword_score(text_norm, keywords)
        
        matches = 0
        for level in ["high", "medium", "low"]:
            for word in keywords[level]:
                if word in text_norm: matches += 1
            
        scores[cat_name] = score
        match_counts[cat_name] = matches

    scores = apply_negative_logic(scores, text_norm)

    if max(scores.values()) == 0:
        return "Gündem", False

    densities = {k: v / word_count for k, v in scores.items()}
    top_cat = max(densities, key=densities.get)
    
    if top_cat != "Gündem":
        if match_counts[top_cat] < 2: 
            top_cat = "Gündem"
        elif densities[top_cat] < (1.8 * densities["Gündem"]): 
            top_cat = "Gündem"

    if ai_category == top_cat: return top_cat, False
    if densities.get(top_cat, 0) > 0.8 and match_counts[top_cat] >= 3:
        return top_cat, True
    
    return ai_category, False