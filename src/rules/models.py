"""
Rule 数据模型
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Rule:
    """
    Rule 数据模型

    Attributes:
        id: 唯一标识符（文件夹名）
        name: 规则名称
        description: 规则描述
        summary: 一句话摘要，用于末尾 reminder 注入
        content: 规则内容（Markdown）
        version: 版本号
        author: 作者
        metadata: 其他元数据
    """
    id: str
    name: str
    description: str
    content: str
    summary: str = ""
    version: str = "1.0"
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "summary": self.summary,
            "content": self.content,
            "version": self.version,
            "author": self.author,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Rule":
        """从字典创建 Rule"""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            summary=data.get("summary", ""),
            content=data.get("content", ""),
            version=data.get("version", "1.0"),
            author=data.get("author", ""),
            metadata=data.get("metadata", {}),
        )
