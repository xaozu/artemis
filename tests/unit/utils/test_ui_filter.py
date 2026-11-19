# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from artemis.utils.ui_filter import (
    _clip_bounds,
    _detect_fixed_system_bars,
    _is_semantic_empty,
    filter_node,
    filter_ui_hierarchy,
)


def test_is_semantic_empty():
    # Interactive or textual nodes are NOT empty
    assert not _is_semantic_empty({"text": "Hello"})
    assert not _is_semantic_empty({"content-desc": "Button"})
    assert not _is_semantic_empty({"clickable": True})
    assert not _is_semantic_empty({"clickable": "true"})
    assert not _is_semantic_empty({"focusable": True})
    assert not _is_semantic_empty({"className": "android.widget.ImageView"})

    # Empty containers ARE semantic empty
    assert _is_semantic_empty(
        {
            "text": "",
            "clickable": False,
            "className": "android.widget.FrameLayout",
        }
    )
    assert _is_semantic_empty({})


def test_clip_bounds():
    node = {"bounds": "[10,-10][1200,100]"}
    bounds = {"left": 10, "top": -10, "right": 1200, "bottom": 100}
    ancestor_bounds = {"left": 0, "top": 0, "right": 1080, "bottom": 2400}
    clipped = _clip_bounds(node, bounds, ancestor_bounds)

    assert clipped["left"] == 10
    assert clipped["top"] == 0
    assert clipped["right"] == 1080
    assert clipped["bottom"] == 100
    assert node["bounds"] == "[10,0][1080,100]"
    assert "is_clipped" not in node


def test_clip_bounds_fully_clipped():
    node = {"bounds": "[1200,2500][1300,2600]"}
    bounds = {"left": 1200, "top": 2500, "right": 1300, "bottom": 2600}
    ancestor_bounds = {"left": 0, "top": 0, "right": 1080, "bottom": 2400}
    clipped = _clip_bounds(node, bounds, ancestor_bounds)

    assert clipped["left"] == 1080
    assert clipped["top"] == 2400
    assert clipped["right"] == 1080
    assert clipped["bottom"] == 2400
    assert node["is_clipped"] is True
    assert node["bounds"] == "[1080,2400][1080,2400]"


def test_semantic_hoisting():
    hierarchy = [
        {
            "bounds": "[0,0][100,100]",
            "text": "",
            "clickable": False,
            "children": [
                {
                    "bounds": "[10,10][90,90]",
                    "text": "Child Button",
                    "clickable": True,
                }
            ],
        }
    ]

    # Mode: hoist
    res_hoist = filter_ui_hierarchy(hierarchy, semantic_pruning="hoist")
    assert len(res_hoist) == 1
    assert res_hoist[0]["text"] == "Child Button"

    # Mode: safe
    res_safe = filter_ui_hierarchy(hierarchy, semantic_pruning="safe")
    assert len(res_safe) == 1
    assert res_safe[0]["text"] == ""
    assert len(res_safe[0]["children"]) == 1


def test_dynamic_min_size():
    # Short side is 1000. 0.5% of 1000 is 5px.
    # Element size is 4x4 -> filtered out.
    node = {"bounds": "[0,0][4,4]", "text": "Tiny", "clickable": True}
    res = filter_node(node, 1000, 2000, min_element_size=2, use_dynamic_min_size=True)
    assert res is None

    # Element size is 6x6 -> kept.
    node = {"bounds": "[0,0][6,6]", "text": "Good", "clickable": True}
    res = filter_node(node, 1000, 2000, min_element_size=2, use_dynamic_min_size=True)
    assert res is not None


def test_ancestor_bounds_propagation():
    hierarchy = {
        "bounds": "[0,0][100,100]",
        "text": "Parent",
        "clickable": True,
        "children": [{"bounds": "[50,50][150,150]", "text": "Child", "clickable": True}],
    }

    # The child is at [50,50][150,150], but parent is [0,0][100,100].
    # So child should be clipped to [50,50][100,100].
    res = filter_node(hierarchy, 1000, 1000, clip_bounds=True)
    assert res is not None
    assert res["children"][0]["bounds"] == "[50,50][100,100]"


def test_sibling_merging():
    hierarchy = {
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.ImageView",
                "bounds": "[10,10][50,50]",
                "clickable": False,
                "focusable": False,
            },
            {
                "className": "android.widget.TextView",
                "text": "Settings",
                "bounds": "[60,10][150,50]",
                "clickable": False,
                "focusable": False,
            },
        ],
    }

    # The parent is clickable, has 1 text child and 1 image child without text.
    # They should be merged!
    res = filter_node(hierarchy, 1000, 1000)
    assert res is not None
    assert "children" in res
    assert len(res["children"]) == 1
    assert res["children"][0]["text"] == "Settings"
    assert res["children"][0]["content-desc"] == "[Icon]"
    assert res["children"][0]["bounds"] == "[10,10][150,50]"


def test_sibling_merging_not_triggered_if_child_clickable():
    hierarchy = {
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.ImageView",
                "bounds": "[10,10][50,50]",
                "clickable": True,  # Child is clickable
                "focusable": False,
            },
            {
                "className": "android.widget.TextView",
                "text": "Settings",
                "bounds": "[60,10][150,50]",
                "clickable": False,
                "focusable": False,
            },
        ],
    }

    res = filter_node(hierarchy, 1000, 1000)
    assert res is not None
    assert "children" in res
    assert len(res["children"]) == 2  # No merge


def test_sibling_merging_not_triggered_if_form_control():
    hierarchy = {
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.CheckBox",  # Form control
                "bounds": "[10,10][50,50]",
                "clickable": True,  # Make it clickable so it's not pruned
                "focusable": False,
            },
            {
                "className": "android.widget.TextView",
                "text": "Enable",
                "bounds": "[60,10][150,50]",
                "clickable": False,
                "focusable": False,
            },
        ],
    }

    res = filter_node(hierarchy, 1000, 1000)
    assert res is not None
    assert "children" in res
    assert len(res["children"]) == 2  # No merge


def test_parent_child_redundancy():
    # Case 1: Redundant TextView removed
    hierarchy = {
        "text": "Confirm",
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.TextView",
                "text": "Confirm",
                "bounds": "[10,10][190,190]",
                "clickable": False,
                "focusable": False,
            }
        ],
    }
    res = filter_node(hierarchy, 1000, 1000)
    assert res is not None
    assert "children" in res
    assert len(res["children"]) == 0  # Child removed

    # Case 2: Redundant CheckBox text cleared
    hierarchy2 = {
        "text": "Confirm",
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.CheckBox",
                "text": "Confirm",
                "bounds": "[10,10][190,190]",
                "clickable": False,
                "focusable": False,
            }
        ],
    }
    res2 = filter_node(hierarchy2, 1000, 1000)
    assert res2 is not None
    assert "children" in res2
    assert len(res2["children"]) == 1
    assert res2["children"][0]["text"] == ""

    # Case 3: Redundant interactive node text cleared
    hierarchy3 = {
        "text": "Confirm",
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.TextView",
                "text": "Confirm",
                "bounds": "[10,10][190,190]",
                "clickable": True,
                "focusable": False,
            }
        ],
    }
    res3 = filter_node(hierarchy3, 1000, 1000)
    assert res3 is not None
    assert "children" in res3
    assert len(res3["children"]) == 1
    assert res3["children"][0]["text"] == ""

    # Case 4: No match
    hierarchy4 = {
        "text": "Confirm",
        "clickable": True,
        "bounds": "[0,0][200,200]",
        "children": [
            {
                "className": "android.widget.TextView",
                "text": "Cancel",
                "bounds": "[10,10][190,190]",
                "clickable": False,
                "focusable": False,
            }
        ],
    }
    res4 = filter_node(hierarchy4, 1000, 1000)
    assert res4 is not None
    assert "children" in res4
    assert len(res4["children"]) == 1
    assert res4["children"][0]["text"] == "Cancel"


def test_fixed_system_bars_clamping_and_discarding():
    # Simulate a full-width fixed bottom navigation bar at [0, 2250][1080, 2400]
    # and two candidates: one slightly occluded (should be safely clamped) and one mostly covered (should be discarded).
    hierarchy = [
        {
            "resource-id": "com.google.android.youtube:id/bottom_navigation",
            "bounds": "[0,2250][1080,2400]",
            "scrollable": False,
            "clickable": False,
        },
        {
            "text": "Partially occluded video item",
            "bounds": "[0,2100][1080,2300]",
            "scrollable": False,
            "clickable": True,
        },
        {
            "text": "Almost entirely covered sliver item",
            "bounds": "[0,2245][1080,2300]",
            "scrollable": False,
            "clickable": True,
        },
    ]

    filtered = filter_ui_hierarchy(
        hierarchy, screen_width=1080, screen_height=2400, min_element_size=20
    )
    # The bottom_navigation bar itself is kept (or filtered depending on semantics, here kept if not empty / checked).
    # Specifically check the clamped item and discarded item:
    texts = [el.get("text") for el in filtered]
    assert "Partially occluded video item" in texts
    assert "Almost entirely covered sliver item" not in texts

    clamped_item = next(el for el in filtered if el.get("text") == "Partially occluded video item")
    # Its original bottom was 2300, clamped safely to the bar's top at 2250
    assert clamped_item["bounds"] == "[0,2100][1080,2250]"


def test_mutual_occlusion_warning_injection():
    from artemis.utils.visualization import format_minimal_list_with_elements

    # Simulate two overlapping non-ancestor interactive items with >= 50% overlap
    fused_xml = [
        {"text": "Search Video Result", "bounds": "[50,1800][1000,2000]"},
        {"text": "Create Floating Button", "bounds": "[750,1850][1000,2000]"},
    ]

    minimal_list, elements, labels = format_minimal_list_with_elements(
        fused_xml, width=1080, height=2400
    )
    assert "WARNING: may overlap with [2], possible occlusion" in minimal_list
    assert "WARNING: may overlap with [1], possible occlusion" in minimal_list


# ---------------------------------------------------------------------------
# 上游 PR #181（issue #180）：系统弹窗（运行时权限弹窗、SystemUI 警告框）必须保持可点击，
# 只有真正细长的状态栏/导航栏才被当作固定栏过滤。
# 夹具使用 parse_hierarchy_xml_to_elements 产出的扁平元素形态（文档顺序、无嵌套）。
# ---------------------------------------------------------------------------

_STATUS_BAR = {
    "package": "com.android.systemui",
    "class": "android.widget.FrameLayout",
    "bounds": "[0,0][1080,80]",
    "clickable": "false",
    "scrollable": "false",
}
_NAV_BAR = {
    "package": "com.android.systemui",
    "class": "android.widget.FrameLayout",
    "bounds": "[0,2268][1080,2400]",
    "clickable": "false",
    "scrollable": "false",
}


def _texts_of(elements):
    return {el.get("text") for el in elements}


def test_permission_controller_dialog_stays_clickable():
    """应用窗口上叠加运行时权限弹窗、且存在真实状态栏/导航栏时，允许/拒绝按钮必须保留且 bounds 不变。"""
    hierarchy = [
        dict(_STATUS_BAR),
        dict(_NAV_BAR),
        # uiautomator 报告的全屏弹窗窗口根节点
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.FrameLayout",
            "bounds": "[0,0][1080,2400]",
            "clickable": "false",
            "scrollable": "false",
        },
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.TextView",
            "text": "Allow Camera to take pictures and record video?",
            "bounds": "[90,1000][990,1080]",
            "clickable": "false",
        },
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.Button",
            "resource-id": "com.android.permissioncontroller:id/permission_allow_button",
            "text": "While using the app",
            "bounds": "[90,1900][990,2000]",
            "clickable": "true",
        },
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.Button",
            "resource-id": "com.android.permissioncontroller:id/permission_deny_button",
            "text": "Deny",
            "bounds": "[90,2100][990,2200]",
            "clickable": "true",
        },
        {
            "package": "com.example.app",
            "class": "android.widget.Button",
            "text": "AppButton",
            "bounds": "[100,300][980,420]",
            "clickable": "true",
        },
    ]

    bars = _detect_fixed_system_bars(hierarchy, 1080, 2400)
    # 只有真实的状态栏和导航栏是固定栏
    assert bars == [
        {"left": 0, "top": 0, "right": 1080, "bottom": 80},
        {"left": 0, "top": 2268, "right": 1080, "bottom": 2400},
    ]

    filtered = filter_ui_hierarchy(hierarchy, screen_width=1080, screen_height=2400)
    texts = _texts_of(filtered)
    assert "While using the app" in texts
    assert "Deny" in texts
    allow = next(el for el in filtered if el.get("text") == "While using the app")
    deny = next(el for el in filtered if el.get("text") == "Deny")
    assert allow["bounds"] == "[90,1900][990,2000]"
    assert deny["bounds"] == "[90,2100][990,2200]"


def test_permission_controller_never_classified_as_bar():
    """贴底、满宽的权限弹窗变体（300px 落在通用底栏高度区间内）也不能被当作固定栏。

    permissioncontroller 只承载弹窗，从不拥有状态栏/导航栏。
    """
    hierarchy = [
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.FrameLayout",
            "bounds": "[0,2100][1080,2400]",
            "clickable": "false",
            "scrollable": "false",
        },
        {
            "package": "com.android.permissioncontroller",
            "class": "android.widget.Button",
            "text": "Allow",
            "bounds": "[90,2200][990,2300]",
            "clickable": "true",
        },
    ]

    assert _detect_fixed_system_bars(hierarchy, 1080, 2400) == []

    filtered = filter_ui_hierarchy(hierarchy, screen_width=1080, screen_height=2400)
    allow = next(el for el in filtered if el.get("text") == "Allow")
    assert allow["bounds"] == "[90,2200][990,2300]"


def test_systemui_full_screen_dialog_window_not_a_bar():
    """SystemUI 警告框（USB 调试授权）位于全屏 SystemUI 窗口内：该窗口不是固定栏，按钮必须可点击。"""
    hierarchy = [
        dict(_STATUS_BAR),
        dict(_NAV_BAR),
        {
            "package": "com.android.systemui",
            "class": "android.widget.FrameLayout",
            "bounds": "[0,0][1080,2400]",
            "clickable": "false",
            "scrollable": "false",
        },
        {
            "package": "com.android.systemui",
            "class": "android.widget.TextView",
            "text": "Allow USB debugging?",
            "bounds": "[90,1000][990,1060]",
            "clickable": "false",
        },
        {
            "package": "com.android.systemui",
            "class": "android.widget.Button",
            "text": "Cancel",
            "bounds": "[90,1340][520,1420]",
            "clickable": "true",
        },
        {
            "package": "com.android.systemui",
            "class": "android.widget.Button",
            "text": "OK",
            "bounds": "[560,1340][990,1420]",
            "clickable": "true",
        },
    ]

    bars = _detect_fixed_system_bars(hierarchy, 1080, 2400)
    assert bars == [
        {"left": 0, "top": 0, "right": 1080, "bottom": 80},
        {"left": 0, "top": 2268, "right": 1080, "bottom": 2400},
    ]

    filtered = filter_ui_hierarchy(hierarchy, screen_width=1080, screen_height=2400)
    texts = _texts_of(filtered)
    assert "Cancel" in texts
    assert "OK" in texts
    ok = next(el for el in filtered if el.get("text") == "OK")
    assert ok["bounds"] == "[560,1340][990,1420]"


def test_systemui_tall_bottom_dialog_panel_not_a_bar():
    """高于屏幕 15% 的贴底满宽 SystemUI 弹窗面板是弹窗而非导航栏。

    修复前 systemui 绕过了高度上限被判为固定栏，伪固定栏会裁剪或静默丢弃跨越弹窗上沿的元素。
    """
    hierarchy = [
        {
            "package": "com.android.systemui",
            "class": "android.widget.FrameLayout",
            "bounds": "[0,1700][1080,2400]",
            "clickable": "false",
            "scrollable": "false",
        },
        {
            "package": "com.android.systemui",
            "class": "android.widget.Button",
            "text": "OK",
            "bounds": "[560,2200][990,2300]",
            "clickable": "true",
        },
        # 跨越弹窗上沿的应用元素
        {
            "package": "com.example.app",
            "class": "android.widget.TextView",
            "text": "CrossingItem",
            "bounds": "[100,1600][980,1750]",
            "clickable": "true",
        },
        # 大部分被弹窗面板覆盖的应用元素
        {
            "package": "com.example.app",
            "class": "android.widget.TextView",
            "text": "CoveredItem",
            "bounds": "[100,1690][980,1800]",
            "clickable": "true",
        },
    ]

    assert _detect_fixed_system_bars(hierarchy, 1080, 2400) == []

    filtered = filter_ui_hierarchy(hierarchy, screen_width=1080, screen_height=2400)
    texts = _texts_of(filtered)
    assert "OK" in texts
    assert "CrossingItem" in texts
    assert "CoveredItem" in texts
    crossing = next(el for el in filtered if el.get("text") == "CrossingItem")
    assert crossing["bounds"] == "[100,1600][980,1750]"


def test_real_status_and_nav_bars_still_filtered():
    """回归保护：真实细长栏仍被识别并裁剪被遮挡元素，包括 SystemUI 的细手势导航条（高度 < 25px）。"""
    hierarchy = [
        dict(_STATUS_BAR),
        dict(_NAV_BAR),
        # 20px 高的手势导航条，低于通用 25px 下限，靠 systemui 高置信身份命中
        {
            "package": "com.android.systemui",
            "class": "android.widget.FrameLayout",
            "bounds": "[0,2380][1080,2400]",
            "clickable": "false",
            "scrollable": "false",
        },
        {
            "package": "com.example.app",
            "class": "android.widget.TextView",
            "text": "Partially occluded item",
            "bounds": "[0,2100][1080,2300]",
            "clickable": "true",
        },
        {
            "package": "com.example.app",
            "class": "android.widget.TextView",
            "text": "Almost entirely covered sliver",
            "bounds": "[0,2260][1080,2300]",
            "clickable": "true",
        },
    ]

    bars = _detect_fixed_system_bars(hierarchy, 1080, 2400)
    assert {"left": 0, "top": 0, "right": 1080, "bottom": 80} in bars
    assert {"left": 0, "top": 2268, "right": 1080, "bottom": 2400} in bars
    assert {"left": 0, "top": 2380, "right": 1080, "bottom": 2400} in bars

    filtered = filter_ui_hierarchy(hierarchy, screen_width=1080, screen_height=2400)
    texts = _texts_of(filtered)
    assert "Partially occluded item" in texts
    assert "Almost entirely covered sliver" not in texts
    clamped = next(el for el in filtered if el.get("text") == "Partially occluded item")
    # 按导航栏上沿 2268 裁剪
    assert clamped["bounds"] == "[0,2100][1080,2268]"
