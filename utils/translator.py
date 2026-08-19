"""PCB-specific bilingual translator with Google Translate API + terminology dictionary."""
from __future__ import annotations

import re
from typing import Optional

import streamlit as st

# PCB / Electronics manufacturing terminology dictionary (kept for reference display)
EN_TO_CN = {
    "rigid": "硬板", "flexible": "柔性板", "flex": "柔性", "fpc": "FPC柔性板",
    "rigid-flex": "刚柔结合板", "multilayer": "多层板", "stackup": "叠层结构",
    "stiffener": "补强", "pi stiffener": "PI补强", "polyimide stiffener": "PI补强",
    "steel stiffener": "钢片补强", "stainless steel stiffener": "不锈钢补强",
    "fr4 stiffener": "FR4补强", "adhesive tape": "粘性胶带", "reinforcement": "补强",
    "hasl": "喷锡", "lead-free hasl": "无铅喷锡", "enig": "沉金", "osp": "OSP",
    "immersion gold": "沉金", "gold finger": "金手指",
    "solder mask": "阻焊层", "coverlay": "覆盖膜", "silkscreen": "丝印",
    "via": "过孔", "through-hole": "通孔", "blind via": "盲孔", "buried via": "埋孔",
    "via-in-pad": "盘中孔", "pth": "镀通孔", "npth": "非镀通孔",
    "impedance control": "阻抗控制", "differential pair": "差分对",
    "emi shield": "EMI屏蔽", "emi shielding": "EMI屏蔽",
    "panelization": "拼板", "v-cut": "V割", "v-score": "V割",
    "mouse bite": "邮票孔", "tab routing": "邮票孔", "fiducial": "基准点",
    "edge plating": "侧面镀铜", "castellated holes": "半孔", "half-hole": "半孔",
    "smt": "贴片", "assembly": "贴片/组装", "bom": "物料清单",
    "reflow": "回流焊", "stencil": "钢网", "solder paste": "锡膏",
    "flying probe": "飞针测试", "e-test": "电测", "aoi": "AOI光学检测",
    "polyimide": "聚酰亚胺", "fr4": "FR4",
    "eta": "预计到达时间", "etd": "预计发货时间", "tracking number": "运单号",
    "engineering question": "工程问题", "eq": "工程问题(EQ)",
    "gerber": "Gerber文件", "drill file": "钻孔文件",
}

CN_TO_EN = {v.lower(): k for k, v in EN_TO_CN.items()}


def google_translate(text: str, target_lang: str, source_lang: str = "") -> str:
    """Translate text using deep-translator (free, no API key needed).

    Args:
        text: text to translate
        target_lang: "zh-CN" or "zh" for Chinese, "en" for English
        source_lang: source language code, or "auto" for auto-detect

    Returns:
        Translated text, or original text if translation fails
    """
    # Normalize language codes
    if target_lang in ("zh", "zh-CN", "chinese"):
        target_lang = "zh-CN"
    if source_lang in ("zh", "zh-CN", "chinese"):
        source_lang = "zh-CN"
    if not source_lang:
        source_lang = "auto"

    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        # deep-translator has a 5000 char limit per call, split if needed
        if len(text) <= 4500:
            return translator.translate(text)
        else:
            # Split by lines and translate in chunks
            lines = text.split("\n")
            chunks = []
            current = []
            current_len = 0
            for line in lines:
                if current_len + len(line) > 4000 and current:
                    chunks.append("\n".join(current))
                    current = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += len(line)
            if current:
                chunks.append("\n".join(current))
            results = [translator.translate(chunk) for chunk in chunks]
            return "\n".join(results)
    except ImportError:
        return f"[deep-translator not installed] {text}"
    except Exception as e:
        return f"[Translation error: {e}] {text}"


@st.cache_data(ttl=3600, show_spinner=False)
def cached_translate(text: str, target_lang: str, source_lang: str = "") -> str:
    """google_translate() memoized for 1h so repeating a translation is instant."""
    return google_translate(text, target_lang, source_lang)


def detect_language(text: str) -> str:
    """Detect if text is primarily Chinese or English."""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_alpha = len(re.findall(r"[a-zA-Z]", text))
    if chinese_chars > total_alpha:
        return "zh"
    return "en"


def translate_with_dict(text: str, direction: str = "auto") -> tuple[str, str, list[tuple[str, str]]]:
    """Translate using PCB dictionary (legacy, for terminology reference)."""
    if direction == "auto":
        direction = "en2cn" if detect_language(text) == "en" else "cn2en"

    dictionary = EN_TO_CN if direction == "en2cn" else CN_TO_EN
    matched_terms = []
    result = text

    sorted_terms = sorted(dictionary.keys(), key=len, reverse=True)
    for term in sorted_terms:
        translation = dictionary[term]
        escaped = re.escape(term)
        if len(term) <= 3:
            pattern = re.compile(r"\b" + escaped + r"\b", re.IGNORECASE)
        else:
            pattern = re.compile(escaped, re.IGNORECASE)
        if pattern.search(result):
            matched_terms.append((term, translation))

    return result, direction, matched_terms


def get_terminology_dict() -> dict:
    """Return the EN->CN terminology dictionary."""
    return EN_TO_CN
