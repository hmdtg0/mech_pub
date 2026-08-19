"""PCB Translator - Google Translate + PCB terminology dictionary."""
import streamlit as st

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.translator import cached_translate, detect_language, get_terminology_dict

st.title("🌐 Translator")

tab1, tab2, tab3 = st.tabs([
    "General Translate",
    "Process Notes (EN→CN)",
    "Terminology Dictionary",
])

# === Tab 1: General Translation ===
with tab1:
    st.header("General PCB Translation")
    st.markdown("Paste any text. Auto-detects language direction.")

    direction = st.radio(
        "Direction",
        ["Auto-detect", "English → Chinese", "Chinese → English"],
        horizontal=True,
    )

    input_text = st.text_area(
        "Input text:",
        height=200,
        placeholder="Paste Slack message, JLC EQ reply, or any PCB-related text here...",
        key="general_input",
    )

    if st.button("🔄 Translate", key="general_translate_btn", type="primary"):
        if input_text.strip():
            if direction == "Auto-detect":
                lang = detect_language(input_text)
                target = "zh" if lang == "en" else "en"
                source = ""
            elif direction == "English → Chinese":
                target = "zh"
                source = "en"
            else:
                target = "en"
                source = "zh"

            with st.spinner("Translating..."):
                result = cached_translate(input_text, target, source)

            st.subheader("Translation Result")
            st.text_area("Output (copy this):", value=result, height=200, key="general_output")
        else:
            st.warning("Please enter text to translate.")

# === Tab 2: Process Notes ===
with tab2:
    st.header("Process Notes Translation (EN → CN)")
    st.markdown("Translate engineering notes for JLC order remarks. Input English, get Chinese output.")

    notes_input = st.text_area(
        "English process notes:",
        height=200,
        placeholder="e.g., PI stiffener on back side, 0.2mm thickness, adhesive tape attachment\nEMI shielding on top side\nImpedance control required",
        key="notes_input",
    )

    if st.button("🔄 Translate to Chinese", key="notes_translate_btn", type="primary"):
        if notes_input.strip():
            with st.spinner("Translating..."):
                result = cached_translate(notes_input, "zh", "en")

            st.subheader("Chinese Output (paste to JLC remarks)")
            st.text_area("Chinese notes:", value=result, height=200, key="cn_output")
        else:
            st.warning("Please enter notes to translate.")

    st.markdown("---")
    st.subheader("EQ Reply Translation (CN → EN)")
    st.markdown("Paste JLC EQ Chinese reply, get English translation to share on Slack.")

    eq_input = st.text_area(
        "Chinese EQ reply:",
        height=150,
        placeholder="粘贴嘉立创的中文EQ回复...",
        key="eq_input",
    )

    if st.button("🔄 Translate to English", key="eq_translate_btn", type="primary"):
        if eq_input.strip():
            with st.spinner("Translating..."):
                result = cached_translate(eq_input, "en", "zh")

            st.subheader("English Output (share on Slack)")
            st.text_area("English translation:", value=result, height=150, key="en_output")
        else:
            st.warning("Please enter text to translate.")

# === Tab 3: Dictionary ===
with tab3:
    st.header("PCB Terminology Dictionary")
    terms = get_terminology_dict()
    st.markdown(f"**{len(terms)} terms** — PCB-specific English ↔ Chinese reference.")

    search = st.text_input("Search term (English or Chinese):")

    categories = {
        "PCB Types & Structure": ["rigid", "flexible", "fpc", "rigid-flex", "multilayer", "stackup"],
        "Stiffener & Reinforcement": ["stiffener", "pi stiffener", "polyimide stiffener", "steel stiffener", "stainless steel stiffener", "fr4 stiffener", "adhesive tape", "reinforcement"],
        "Surface Finish": ["hasl", "lead-free hasl", "enig", "osp", "immersion gold", "gold finger"],
        "Solder Mask & Silkscreen": ["solder mask", "coverlay", "silkscreen"],
        "Drilling & Vias": ["via", "through-hole", "blind via", "buried via", "via-in-pad", "pth", "npth"],
        "Impedance & Electrical": ["impedance control", "differential pair"],
        "EMI & Shielding": ["emi shield", "emi shielding"],
        "Manufacturing": ["panelization", "v-cut", "v-score", "mouse bite", "tab routing", "fiducial", "edge plating", "castellated holes", "half-hole"],
        "SMT / Assembly": ["smt", "assembly", "bom", "reflow", "stencil", "solder paste"],
        "Testing": ["flying probe", "e-test", "aoi"],
        "Shipping": ["eta", "etd", "tracking number"],
        "EQ Related": ["engineering question", "eq", "gerber", "drill file"],
    }

    for cat_name, cat_terms in categories.items():
        filtered = []
        for t in cat_terms:
            cn = terms.get(t, "")
            if search:
                if search.lower() in t.lower() or search in cn:
                    filtered.append((t, cn))
            else:
                if cn:
                    filtered.append((t, cn))

        if filtered:
            with st.expander(f"{cat_name} ({len(filtered)})"):
                for en, cn in filtered:
                    st.markdown(f"- **{en}** → {cn}")
