"""精简观测必须暴露带标签开关控件的显式 XML 状态（移植自上游 PR #193，吸收 #189 的部分用例）。

修复前开、关两个状态的带标签 Switch 渲染出完全相同的行：checked/enabled 属性在 XML 与
模型可见文本之间被丢弃。缺失或非法的属性必须渲染为未知，而不是默认当作关闭；非开关控件
在真实 uiautomator dump 中也带 checked="false" enabled="true"，不能因此给每一行加状态噪声。
"""

from copy import deepcopy

import pytest

from artemis.utils.visualization import format_minimal_list_with_elements
from third_party.mobile_use.clients.ui_automator_client import parse_hierarchy_xml_to_elements


def _switch(**kw):
    node = {
        "class": "android.widget.Switch",
        "content-desc": "Dark theme",
        "resource-id": "com.android.settings:id/switchWidget",
        "checkable": "true",
        "checked": "false",
        "enabled": "true",
        "bounds": "[901,1661][1038,1787]",
    }
    node.update(kw)
    return node


def _render(nodes):
    return format_minimal_list_with_elements(nodes, 1080, 2400)


def test_on_and_off_switch_render_differently():
    """开、关两种状态的同一开关必须渲染出不同文本。"""
    on_text, _, _ = _render([_switch(checked="true")])
    off_text, _, _ = _render([_switch(checked="false")])
    assert on_text != off_text
    assert "checked:true" in on_text
    assert "checked:false" in off_text


@pytest.mark.parametrize("widget", ["Switch", "CheckBox"])
@pytest.mark.parametrize("checked", ["true", "false"])
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_checkable_controls_render_independent_checked_and_enabled(widget, checked, enabled):
    """checked 与 enabled 相互独立地渲染，且不修改输入节点。"""
    node = _switch(**{"class": f"android.widget.{widget}", "checked": checked, "enabled": enabled})
    original = deepcopy(node)
    text, _, _ = _render([node])
    assert text == (
        f"[1] Text: 'Dark theme' | Bounds: [834,692][961,744] | checked:{checked} enabled:{enabled}"
    )
    assert node == original


def test_missing_checked_renders_unknown():
    """可勾选控件缺少 checked 时渲染为未知，而不是默认关闭。"""
    node = _switch()
    del node["checked"]
    text, _, _ = _render([node])
    assert "checked:?" in text
    assert "checked:false" not in text


def test_invalid_checked_renders_unknown():
    """非法的 checked 值渲染为未知。"""
    text, _, _ = _render([_switch(checked="yes")])
    assert "checked:?" in text


def test_disabled_noncheckable_element_shows_enabled_false():
    """非开关控件被禁用时只额外输出 enabled:false。"""
    node = {
        "class": "android.widget.TextView",
        "text": "Advanced",
        "enabled": "false",
        "bounds": "[0,0][500,100]",
    }
    text, _, _ = _render([node])
    assert text.endswith(" | enabled:false")
    assert "checked" not in text


def test_enabled_noncheckable_element_has_no_state_noise():
    """普通可用元素不输出任何状态。"""
    node = {
        "class": "android.widget.TextView",
        "text": "General",
        "enabled": "true",
        "bounds": "[0,0][500,100]",
    }
    text, _, _ = _render([node])
    assert "checked" not in text
    assert "enabled" not in text


def test_real_uiautomator_dump_noncheckable_has_no_state_noise():
    """真实 uiautomator dump 中每个节点都带 checkable/checked/enabled，非开关控件不能因此被加上状态。"""
    xml = (
        '<?xml version="1.0"?><hierarchy rotation="0">'
        '<node index="0" text="General" resource-id="" class="android.widget.TextView"'
        ' package="app" content-desc="" checkable="false" checked="false" clickable="true"'
        ' enabled="true" bounds="[0,0][500,100]" />'
        '<node index="1" text="" resource-id="" class="android.widget.Switch"'
        ' package="app" content-desc="Wi-Fi" checkable="true" checked="true" clickable="true"'
        ' enabled="true" bounds="[0,200][500,300]" />'
        "</hierarchy>"
    )
    text, _, _ = format_minimal_list_with_elements(parse_hierarchy_xml_to_elements(xml), 1000, 1000)
    assert text.splitlines() == [
        "[1] Text: 'General' | Bounds: [0,0][500,100]",
        "[2] Text: 'Wi-Fi' | Bounds: [0,200][500,300] | checked:true enabled:true",
    ]


def test_hint_and_error_keep_disabled_state():
    """提示与错误信息存在时仍保留禁用状态。"""
    node = {
        "hint": "Email",
        "error": "Required",
        "enabled": "false",
        "bounds": "[0,0][100,100]",
    }
    text, elements, _ = format_minimal_list_with_elements([node], 1000, 1000)
    assert text == "[1] Hint: 'Email' | Bounds: [0,0][100,100] | enabled:false | Error: 'Required'"
    assert elements[0]["is_hint"] is True
    assert elements[0]["error"] == "Required"


def test_state_carried_on_element_dict():
    """原始状态属性随元素字典携带，供代码侧校验。"""
    _, elements, _ = _render([_switch(checked="true")])
    assert elements[0]["checked"] == "true"
    assert elements[0]["enabled"] == "true"


def test_ocr_fragments_do_not_inherit_parent_control_state():
    """OCR 片段不继承父节点状态，序号与中心点不受影响。"""
    nodes = [
        {
            "text": "Parent",
            "bounds": "[0,0][200,200]",
            "checkable": "true",
            "checked": "true",
            "enabled": "false",
            "ocr_elements": [{"text": "Read from pixels", "bounds": "[10,10][90,90]"}],
        },
        {"text": "Next", "bounds": "[300,300][400,400]", "enabled": "true"},
    ]
    original = deepcopy(nodes)
    text, elements, labels = format_minimal_list_with_elements(nodes, 1000, 1000)
    assert text.splitlines() == [
        "[1] OCR Text: 'Read from pixels' | Bounds: [10,10][90,90]",
        "[2] Text: 'Next' | Bounds: [300,300][400,400]",
    ]
    assert labels == ["1", "2"]
    assert [element["center"] for element in elements] == [[50, 50], [350, 350]]
    assert "checked" not in elements[0]
    assert "enabled" not in elements[0]
    assert nodes == original


def test_identity_index_and_bounds_preserved():
    """控件的标签、序号、bounds 与 resource_id 保持不变。"""
    text, elements, labels = _render([_switch(checked="true")])
    assert "'Dark theme'" in text
    assert "[834,692][961,744]" in text
    assert elements[0]["index"] == 1
    assert elements[0]["resource_id"] == "com.android.settings:id/switchWidget"
    assert labels == ["1"]
