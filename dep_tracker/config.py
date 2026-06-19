"""
配置管理模块
负责加载和解析多仓库配置
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class RepositoryConfig:
    """仓库配置"""
    name: str
    path: str
    type: str = "backend"
    api_contract_patterns: List[str] = field(default_factory=list)
    shared_model_patterns: List[str] = field(default_factory=list)

    def resolve_path(self, base_dir: Optional[str] = None) -> str:
        """解析仓库路径为绝对路径"""
        path = Path(self.path)
        if not path.is_absolute() and base_dir:
            path = Path(base_dir) / path
        return str(path.resolve())


@dataclass
class DependencyRule:
    """依赖规则"""
    from_repo: str
    to_repo: str
    reason: str = ""


@dataclass
class AppConfig:
    """应用配置"""
    repositories: List[RepositoryConfig] = field(default_factory=list)
    dependency_rules: List[DependencyRule] = field(default_factory=list)
    config_file: str = ""

    def get_repo(self, name: str) -> Optional[RepositoryConfig]:
        """根据名称获取仓库配置"""
        for repo in self.repositories:
            if repo.name == name:
                return repo
        return None


class ConfigLoader:
    """配置加载器"""

    @staticmethod
    def load(config_path: str) -> AppConfig:
        """从 YAML 文件加载配置"""
        config_file = Path(config_path).resolve()
        
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_file}")

        with open(config_file, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)

        if not raw_config:
            raise ValueError(f"配置文件为空: {config_file}")

        base_dir = str(config_file.parent)
        repositories = []
        for repo_data in raw_config.get("repositories", []):
            repo = RepositoryConfig(
                name=repo_data["name"],
                path=repo_data.get("path", ""),
                type=repo_data.get("type", "backend"),
                api_contract_patterns=repo_data.get("api_contract_patterns", []),
                shared_model_patterns=repo_data.get("shared_model_patterns", []),
            )
            repo.path = repo.resolve_path(base_dir)
            repositories.append(repo)

        dependency_rules = []
        for rule_data in raw_config.get("dependency_rules", []):
            rule = DependencyRule(
                from_repo=rule_data["from"],
                to_repo=rule_data["to"],
                reason=rule_data.get("reason", ""),
            )
            dependency_rules.append(rule)

        return AppConfig(
            repositories=repositories,
            dependency_rules=dependency_rules,
            config_file=str(config_file),
        )

    @staticmethod
    def find_default_config() -> Optional[str]:
        """查找默认配置文件"""
        search_paths = [
            Path.cwd() / "config.yaml",
            Path.cwd() / "config.yml",
            Path.home() / ".dep_tracker" / "config.yaml",
            Path.home() / ".dep_tracker" / "config.yml",
        ]
        
        for path in search_paths:
            if path.exists():
                return str(path)
        return None
