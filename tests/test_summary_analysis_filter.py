"""Guard the AI-analysis section of generated summaries.

Two things are asserted here:

1. `inject_ai_analysis()` assembles `### 🤖 Yapay Zeka Analizi` in code.
   The section is a contract, not decoration — `routes.py:287` builds the FAQ
   JSON-LD by splitting on that exact marker, and it is the reader-facing
   disclosure that the analysis is AI-written. Asking the model to emit it in
   prose was tried and failed: 6 of 9 live summaries dropped it, because the
   same prompt tells the model not to pad thin sections and it classified the
   analysis heading as padding.

2. `is_empty_analysis()` rejects significance-talk. Three prompt revisions could
   not suppress it — each banned phrase was replaced by an equivalent — so the
   rule lives in code where it is deterministic.

Every case below is real output taken from production on 2026-07-26, labelled by
hand. Run: python3 -m pytest tests/test_summary_analysis_filter.py -v
       or: python3 tests/test_summary_analysis_filter.py
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    # The worker containers have no pytest. Provide just enough of the API for
    # the decorators below, so this file still runs with plain python3 inside a
    # container (see __main__ at the bottom).
    class _ParamMark:
        @staticmethod
        def parametrize(argname, values):
            def deco(fn):
                fn._params = (argname, values)
                return fn
            return deco

    class _PytestStub:
        mark = _ParamMark()

        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

from app.workers.summarizer import (
    inject_ai_analysis,
    is_empty_analysis,
    _AI_HEADING_TR,
    _AI_HEADING_FA,
    _SOURCES_HEADING_TR,
    _SOURCES_HEADING_FA,
)

# ── analyses that must SURVIVE: they carry a fact a reader can use ───────────
KEEP = [
    "Yakalanan hükümlünün cezasının infazı için adli işlemleri başlatıldı.",
    "Max Verstappen'in Macaristan Grand Prix'sini kazanması, Red Bull takımının bu "
    "sezonki üstünlüğünü pekiştirdi. Verstappen, şampiyona liderliğini daha da güçlendirdi.",
    "Köylüler kavşağın yerinin değiştirilmesini istiyor; Karayolları'ndan konuyla "
    "ilgili bir açıklama gelmedi.",
    "Aracın seri üretime geçip geçmeyeceği ve çiftçiye hangi fiyattan sunulacağı "
    "henüz belli değil.",
    "Kaç köyün etkilendiği ve hasarın boyutu henüz açıklanmadı.",
    "Şenliğe 12 bin kişi katıldı; etkinlik bu yıl 27. kez düzenlendi.",
    "Yarışı Ahmet Yılmaz kazandı, genel klasmanda ilk sıraya yükseldi.",
    "Duruşmanın 14 Ekim'de görülmesi bekleniyor.",
    "Yasanın önümüzdeki hafta Meclis'e sunulması bekleniyor.",
    "Kararın Danıştay tarafından incelenmesi bekleniyor.",
    "İddiaların doğruluğu ve Yunanistan'ın resmi tutumu henüz netleşmedi.",
]

# ── analyses that must be DROPPED: they state significance, not facts ────────
DROP = [
    "Türkiye'nin uluslararası işbirliği çerçevesinde orman yangınlarıyla mücadele "
    "eden ülkelere destek olması, hem bölgesel hem de küresel düzeyde olası "
    "felaketlerin önlenmesi açısından önem taşıyor.",
    "Kurslar, çocukların yaz tatilini verimli geçirmeleri ve spor alışkanlığı "
    "kazanmaları için bir fırsat sunuyor.",
    "Faik Öztrak'ın Grup Başkanı seçilmesi, CHP'deki liderlik değişimlerinin ve "
    "parti içi dengelerin bir yansıması olarak değerlendirilebilir.",
    "Bu tür altyapı çalışmaları, yerel yönetimlerin vatandaş odaklı hizmet "
    "anlayışını yansıtıyor.",
    "Bu durum, projelerin uygulanmasında şeffaflık ve katılımcılık eksikliğini "
    "gözler önüne seriyor.",
    "Doğu Karadeniz'in ihracatındaki bu artış, bölgenin ekonomik büyümesine işaret "
    "ediyor. Gelecek dönemde hangi sektörlerin öne çıkacağı merak konusu.",
    "Küresel çatışmaların etkili bir liderlik eksikliğinden kaynaklandığına dair bir "
    "analiz sunuluyor.",
    "Yarışın tamamlanmasıyla sporcuların genel klasmandaki konumları netleşmiş oldu.",
    "Harşena-A05'in bu alanda başarılı olması, Türkiye'nin teknolojik yetkinliğini "
    "pekiştirecektir.",
    "Bursaspor'un Shakhtar Donetsk ile yapacağı hazırlık maçı, takımın güncel form "
    "durumunu gözlemlemek için bir fırsat sunuyor.",
    "DEM Parti'nin teklifi, siyasi süreçlerin ne yönde ilerlediğine dair önemli bir "
    "gösterge niteliği taşıyor.",
    "Emeklilik Planı filminin gösterimi, TRT 1'in izleyici kitlesini genişletme "
    "stratejisinin bir parçası olarak öne çıkıyor.",
    "Netanyahu'nun açıklaması savunma mekanizmasını yansıtmaktadır.",
    "",
    None,
    "Durum sürüyor.",  # under the length floor
]

# Documented blind spots. The filter is high-precision by design: it must never
# delete a usable analysis, so it does not try to catch these. Separating
# "Kararın Danıştay tarafından incelenmesi bekleniyor" (keep) from "Bursaspor'un
# maçı ... bir fırsat sunuyor" (drop) needs to know whether the named actor is
# doing something, which no pattern can see. A context-sensitive tier was built
# for this and removed again — gating on dates killed the Danıştay case, gating
# on proper nouns disabled the tier entirely.
#
# If these start mattering, the next step is a judge model call, not more regex.
KNOWN_MISSES = [
    "Trafik akışının iyileşmesi bekleniyor.",
    "Gelişmelerin yakından takip edilmesi bekleniyor.",
    "Sürecin olumlu sonuçlanması bekleniyor.",
]


@pytest.mark.parametrize("text", KEEP)
def test_concrete_analysis_survives(text):
    assert not is_empty_analysis(text), f"good analysis wrongly dropped: {text[:70]}"


@pytest.mark.parametrize("text", DROP)
def test_hollow_analysis_dropped(text):
    assert is_empty_analysis(text), f"hollow analysis wrongly kept: {str(text)[:70]}"


@pytest.mark.parametrize("text", KNOWN_MISSES)
def test_known_misses_are_still_missed(text):
    """Pins the documented blind spots.

    If one of these starts failing, the filter got stricter — check it did not
    also start eating the KEEP cases, then move the case up to DROP.
    """
    assert not is_empty_analysis(text)


def test_section_inserted_before_sources():
    s = "### ⚡ Özet\nLede.\n\n### Detay\nBody.\n\n### 🔗 Kaynaklar\n- AA"
    out = inject_ai_analysis(s, "Duruşma 14 Ekim'de görülecek.", _AI_HEADING_TR, _SOURCES_HEADING_TR)
    assert _AI_HEADING_TR in out
    assert out.index(_AI_HEADING_TR) < out.index(_SOURCES_HEADING_TR)
    assert out.rstrip().endswith("- AA"), "sources must stay last"
    assert "### Detay\nBody." in out, "original body must be untouched"


def test_appended_when_no_sources_section():
    s = "### ⚡ Özet\nLede."
    out = inject_ai_analysis(s, "Duruşma 14 Ekim'de görülecek.", _AI_HEADING_TR, _SOURCES_HEADING_TR)
    assert out.rstrip().endswith("Duruşma 14 Ekim'de görülecek.")


def test_model_emitted_section_is_not_duplicated():
    s = f"### ⚡ Özet\nLede.\n\n{_AI_HEADING_TR}\nOrijinal.\n\n### 🔗 Kaynaklar\n- AA"
    out = inject_ai_analysis(s, "Yeni metin.", _AI_HEADING_TR, _SOURCES_HEADING_TR)
    assert out.count(_AI_HEADING_TR) == 1
    assert "Orijinal." in out and "Yeni metin." not in out


@pytest.mark.parametrize("analysis", ["", None, "   \n "])
def test_no_section_without_analysis(analysis):
    s = "### ⚡ Özet\nLede.\n\n### 🔗 Kaynaklar\n- AA"
    assert inject_ai_analysis(s, analysis, _AI_HEADING_TR, _SOURCES_HEADING_TR) == s


def test_empty_summary_is_safe():
    assert inject_ai_analysis("", "A." * 20, _AI_HEADING_TR, _SOURCES_HEADING_TR) == ""
    assert inject_ai_analysis(None, "A." * 20, _AI_HEADING_TR, _SOURCES_HEADING_TR) is None


def test_persian_variant_uses_persian_markers():
    fa = "### ⚡ خلاصه\nلید.\n\n### 🔗 منابع\n- AA"
    out = inject_ai_analysis(fa, "دادگاه ۱۴ اکتبر برگزار می‌شود.", _AI_HEADING_FA, _SOURCES_HEADING_FA)
    assert _AI_HEADING_FA in out
    assert out.index(_AI_HEADING_FA) < out.index(_SOURCES_HEADING_FA)
    assert out.rstrip().endswith("- AA")


def test_routes_faq_extraction_still_works():
    """routes.py:287 splits on the bare marker text to build the FAQ JSON-LD."""
    s = "### ⚡ Özet\nLede.\n\n### 🔗 Kaynaklar\n- AA"
    out = inject_ai_analysis(s, "Duruşma 14 Ekim'de görülecek.", _AI_HEADING_TR, _SOURCES_HEADING_TR)
    extracted = out.split("🤖 Yapay Zeka Analizi")[-1].replace("#", "").strip()
    assert extracted.startswith("Duruşma 14 Ekim'de görülecek.")


def _run_standalone() -> int:
    """Minimal runner for containers without pytest. Same assertions, same names."""
    failures = []
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        argname, values = getattr(fn, "_params", (None, [None]))
        for value in values:
            ran += 1
            label = f"{name}[{str(value)[:45]}]" if argname else name
            try:
                fn(value) if argname else fn()
                print(f"PASS  {label}")
            except AssertionError as e:
                failures.append((label, str(e)))
                print(f"FAIL  {label}: {e}")

    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
