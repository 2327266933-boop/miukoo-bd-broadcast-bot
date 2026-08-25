import string
from typing import Dict, Iterable, Set


class TemplateError(ValueError):
    pass


DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "inventory_check": {
        "initial": (
            "Hi {name}，请帮忙确认 {city} 负责门店的库存情况。\n\n"
            "本次涉及 {shop_count} 家门店，请在 {deadline} 前回复确认结果。\n"
            "回复“已确认”或直接说明异常情况即可。"
        ),
        "follow_up": (
            "Hi {name}，刚才的库存确认还没有收到回复。\n\n"
            "请在 {deadline} 前同步一下进展；如已有异常，也可以直接回复异常原因。"
        ),
    },
    "price_lose_follow": {
        "initial": (
            "Hi {name}，{shop_name} 的 {sku_name} 当前命中价格 Lose。\n\n"
            "原因：{lose_reason}\n请在 {deadline} 前确认处理方案。"
        ),
        "follow_up": (
            "Hi {name}，{shop_name} 的 {sku_name} 价格 Lose 跟进还没有收到回复。\n\n"
            "请在 {deadline} 前同步处理进展。"
        ),
    },
    "campaign_signup": {
        "initial": (
            "Hi {name}，{campaign_name} 报名已开启。\n\n"
            "本次权益：{benefit}\n请在 {signup_deadline} 前确认是否报名。"
        ),
        "follow_up": (
            "Hi {name}，{campaign_name} 报名确认还没有收到回复。\n\n"
            "请在 {signup_deadline} 前同步是否参加。"
        ),
    },
    "material_collect": {
        "initial": (
            "Hi {name}，{material_name} 还需要补充以下信息：{missing_fields}。\n\n"
            "请在 {deadline} 前补齐或回复当前卡点。"
        ),
        "follow_up": (
            "Hi {name}，{material_name} 的资料补充还没有收到回复。\n\n"
            "请在 {deadline} 前同步补充结果或卡点。"
        ),
    },
    "task_urge": {
        "initial": (
            "Hi {name}，请跟进任务：{task_title}。\n\n"
            "负责人：{owner_name}\n截止时间：{deadline}\n请回复当前进展。"
        ),
        "follow_up": (
            "Hi {name}，任务“{task_title}”还没有收到进展回复。\n\n"
            "请在 {deadline} 前同步当前状态。"
        ),
    },
    "merchant_follow_up": {
        "initial": (
            "Hi {name}，请帮忙跟进以下 {merchant_count} 个商家：\n\n"
            "{merchant_names}\n\n"
            "处理后直接回复当前进展即可。"
        ),
        "follow_up": (
            "Hi {name}，上面 {merchant_count} 个商家的跟进还没有收到回复：\n\n"
            "{merchant_names}\n\n"
            "有进展请直接回复。"
        ),
    },
}


class TemplateStore:
    def __init__(self, templates: Dict[str, Dict[str, str]] = None) -> None:
        self._templates = templates or DEFAULT_TEMPLATES

    def message_types(self) -> Iterable[str]:
        return sorted(self._templates.keys())

    def required_variables(self, message_type: str, message_kind: str) -> Set[str]:
        template = self._get_template(message_type, message_kind)
        formatter = string.Formatter()
        return {
            field_name
            for _, field_name, _, _ in formatter.parse(template)
            if field_name
        }

    def render(
        self,
        message_type: str,
        message_kind: str,
        variables: Dict[str, object],
    ) -> str:
        template = self._get_template(message_type, message_kind)
        required = self.required_variables(message_type, message_kind)
        missing = sorted(name for name in required if name not in variables)
        if missing:
            raise TemplateError(
                "Missing template variables for {}.{}: {}".format(
                    message_type,
                    message_kind,
                    ", ".join(missing),
                )
            )
        return template.format(**variables)

    def _get_template(self, message_type: str, message_kind: str) -> str:
        if message_type not in self._templates:
            raise TemplateError("Unknown message_type: {}".format(message_type))
        templates_for_type = self._templates[message_type]
        if message_kind not in templates_for_type:
            raise TemplateError(
                "Unknown message kind {} for message_type {}".format(
                    message_kind,
                    message_type,
                )
            )
        return templates_for_type[message_kind]
