"""No brand name reaches a merchant of a different brand.

Every bug this file guards against had the same shape: a value that was fine
while one brand existed -- a module-level default, a hardcoded label, a literal
in an error string -- and became a leak the moment a second brand shared the
deployment. They are invisible in normal use, because the one brand they name
is usually the one looking.

So these are structural checks, not behavioural ones. They read the source and
the resolved defaults rather than driving a request, because the failure is
"this string exists at all", not "this request returned it".
"""

import ast
import io
import os
import re
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Names that must never appear in a string a merchant can read. Deliberately
# includes our own product: a whitelabel merchant has no idea what DataMind is.
BRAND_WORDS = re.compile(r"sales\s*play|sellmo|datamind|nvision", re.I)


def _strings_reaching_merchants(path):
    """Every string literal passed as `detail=` or as a provider-visible label.

    `detail` is what FastAPI puts in the response body and what the widget
    renders in its error box. The token `name` is written into the merchant's
    own provider account, where they read it in their backoffice.
    """
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    found = []

    def literals(node):
        """Constant strings inside a node, including implicit concatenation."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield sub.value, sub.lineno

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords or []:
            if kw.arg == "detail":
                found.extend((v, ln, "detail") for v, ln in literals(kw.value))
            if kw.arg == "json":
                # {"name": ...} on a provider call is a merchant-visible label.
                for sub in ast.walk(kw.value):
                    if isinstance(sub, ast.Dict):
                        for k, v in zip(sub.keys, sub.values):
                            if isinstance(k, ast.Constant) and k.value == "name":
                                found.extend((s, ln, "provider label")
                                             for s, ln in literals(v))
    return found


def test_no_brand_name_in_merchant_facing_strings():
    path = os.path.join(BACKEND, "embed.py")
    leaks = [(s, ln, kind) for s, ln, kind in _strings_reaching_merchants(path)
             if BRAND_WORDS.search(s)]
    assert not leaks, "embed.py has brand names in merchant-facing text:\n" + "\n".join(
        "  line %d (%s): %r" % (ln, kind, s) for s, ln, kind in leaks
    )


def test_error_templates_take_the_company_from_the_brand():
    """The seven merchant-facing templates must be filled, never hardcoded."""
    import embed
    templates = [v for k, v in vars(embed).items()
                 if k.startswith("ERR_") and isinstance(v, str)]
    assert templates, "no ERR_ templates found -- did they get renamed?"
    for t in templates:
        assert not BRAND_WORDS.search(t), "template names a brand: %r" % t


def test_no_default_falls_back_to_a_brand_name():
    """A default that names a brand surfaces in another brand's widget the
    moment a row is missing or deactivated -- which is exactly when being told
    someone else's name is worst."""
    import embed, main
    assert not BRAND_WORDS.search(str(embed._BRAND_DEFAULTS))
    # No brand row at all: the unbranded fallback path.
    assert not BRAND_WORDS.search(main._brand_app_name({}))
    assert not BRAND_WORDS.search(main._brand_company_name({}))


def test_scrub_rewrites_provider_text_to_the_reader_s_brand():
    """Provider faults are forwarded so the merchant sees the real problem --
    but attributed to their own brand, not the integration's."""
    import embed
    partner = {"partner_name": "Sellmo",
               "branding": {"company_name": "Sellmo POS", "brand_slug": "sellmo"}}
    for raw in ("SalesPlay API is down", "salesplaypos returned an error",
                "Sales Play session invalid"):
        out = embed._scrub_brand(raw, partner)
        assert not BRAND_WORDS.search(out.replace("Sellmo POS", "")), \
            "scrub left a brand name in %r -> %r" % (raw, out)
        assert "Sellmo POS" in out
