"""
变更检测模块
负责深入分析 API 契约文件和共享模型的具体变更内容
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from .git_scanner import CommitInfo, RepoScanResult


@dataclass
class ContractChange:
    """契约变更详情"""
    file_path: str
    change_type: str
    affected_items: List[str] = field(default_factory=list)
    breaking: bool = False
    description: str = ""


@dataclass
class ModelChange:
    """模型变更详情"""
    file_path: str
    change_type: str
    affected_models: List[str] = field(default_factory=list)
    breaking: bool = False
    description: str = ""


@dataclass
class ChangeAnalysis:
    """变更分析结果"""
    repo_name: str
    api_contract_changes: List[ContractChange] = field(default_factory=list)
    shared_model_changes: List[ModelChange] = field(default_factory=list)
    commits: List[CommitInfo] = field(default_factory=list)

    @property
    def has_api_changes(self) -> bool:
        return len(self.api_contract_changes) > 0

    @property
    def has_model_changes(self) -> bool:
        return len(self.shared_model_changes) > 0

    @property
    def has_changes(self) -> bool:
        return len(self.commits) > 0

    @property
    def has_breaking_changes(self) -> bool:
        return (
            any(c.breaking for c in self.api_contract_changes) or
            any(m.breaking for m in self.shared_model_changes)
        )

    @property
    def all_affected_items(self) -> Set[str]:
        items = set()
        for c in self.api_contract_changes:
            items.update(c.affected_items)
        for m in self.shared_model_changes:
            items.update(m.affected_models)
        return items


class ChangeDetector:
    """变更检测器"""

    PROTO_FIELD_PATTERN = re.compile(r'\s*(?:repeated\s+)?(?:optional\s+)?(?:required\s+)?(\w+)\s+(\w+)\s*=\s*\d+')
    PROTO_SERVICE_PATTERN = re.compile(r'\s*service\s+(\w+)\s*\{')
    PROTO_RPC_PATTERN = re.compile(r'\s*rpc\s+(\w+)\s*\(')
    PROTO_MESSAGE_PATTERN = re.compile(r'\s*message\s+(\w+)\s*\{')

    OPENAPI_PATH_PATTERN = re.compile(r'^\s*[/\"]?([\w/{}]+)[/\"]?\s*:?\s*$')

    def detect_changes(
        self,
        scan_result: RepoScanResult,
        repo_path: str
    ) -> ChangeAnalysis:
        """检测仓库的详细变更"""
        analysis = ChangeAnalysis(
            repo_name=scan_result.repo_name,
            commits=scan_result.commits,
        )

        all_changed_files: Dict[str, List[CommitInfo]] = {}
        for commit in scan_result.commits:
            for file_path in commit.changed_files:
                if file_path not in all_changed_files:
                    all_changed_files[file_path] = []
                all_changed_files[file_path].append(commit)

        for file_path in scan_result.api_contract_changes:
            change = self._analyze_contract_file(
                file_path,
                repo_path,
                all_changed_files.get(file_path, [])
            )
            if change:
                analysis.api_contract_changes.append(change)

        for file_path in scan_result.shared_model_changes:
            change = self._analyze_model_file(
                file_path,
                repo_path,
                all_changed_files.get(file_path, [])
            )
            if change:
                analysis.shared_model_changes.append(change)

        return analysis

    def _analyze_contract_file(
        self,
        file_path: str,
        repo_path: str,
        commits: List[CommitInfo]
    ) -> Optional[ContractChange]:
        """分析 API 契约文件"""
        full_path = Path(repo_path) / file_path
        
        if not full_path.exists():
            return ContractChange(
                file_path=file_path,
                change_type="deleted",
                breaking=True,
                description="文件已被删除",
                affected_items=[file_path],
            )

        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ContractChange(
                file_path=file_path,
                change_type="modified",
                description="无法读取文件内容",
                affected_items=[file_path],
            )

        suffix = full_path.suffix.lower()
        affected_items = []
        breaking = False
        change_type = "modified"

        if suffix in (".proto",):
            affected_items = self._parse_proto_contract(content)
        elif suffix in (".yaml", ".yml", ".json"):
            affected_items = self._parse_openapi_contract(content, suffix)
        elif suffix in (".ts", ".js"):
            affected_items = self._parse_ts_api_def(content)
        else:
            affected_items = [file_path]

        for commit in commits:
            if self._is_breaking_change(commit.message):
                breaking = True
                break

        return ContractChange(
            file_path=file_path,
            change_type=change_type,
            affected_items=affected_items,
            breaking=breaking,
            description=f"涉及 {len(affected_items)} 个 API 定义变更",
        )

    def _analyze_model_file(
        self,
        file_path: str,
        repo_path: str,
        commits: List[CommitInfo]
    ) -> Optional[ModelChange]:
        """分析共享模型文件"""
        full_path = Path(repo_path) / file_path
        
        if not full_path.exists():
            return ModelChange(
                file_path=file_path,
                change_type="deleted",
                breaking=True,
                description="模型文件已被删除",
                affected_models=[file_path],
            )

        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ModelChange(
                file_path=file_path,
                change_type="modified",
                description="无法读取文件内容",
                affected_models=[file_path],
            )

        suffix = full_path.suffix.lower()
        affected_models = []
        breaking = False

        if suffix in (".py",):
            affected_models = self._parse_python_models(content)
        elif suffix in (".go",):
            affected_models = self._parse_go_models(content)
        elif suffix in (".ts", ".js"):
            affected_models = self._parse_ts_models(content)
        elif suffix in (".proto",):
            affected_models = self._parse_proto_models(content)
        else:
            affected_models = [file_path]

        for commit in commits:
            if self._is_breaking_change(commit.message):
                breaking = True
                break

        return ModelChange(
            file_path=file_path,
            change_type="modified",
            affected_models=affected_models,
            breaking=breaking,
            description=f"涉及 {len(affected_models)} 个模型定义变更",
        )

    def _parse_proto_contract(self, content: str) -> List[str]:
        """解析 Protobuf 契约"""
        items = []
        
        for match in self.PROTO_SERVICE_PATTERN.finditer(content):
            items.append(f"Service: {match.group(1)}")
        
        for match in self.PROTO_RPC_PATTERN.finditer(content):
            items.append(f"RPC: {match.group(1)}")
        
        return items

    def _parse_proto_models(self, content: str) -> List[str]:
        """解析 Protobuf 模型"""
        models = []
        
        for match in self.PROTO_MESSAGE_PATTERN.finditer(content):
            models.append(f"Message: {match.group(1)}")
        
        return models

    def _parse_openapi_contract(self, content: str, suffix: str) -> List[str]:
        """解析 OpenAPI/Swagger 契约"""
        items = []
        
        try:
            if suffix == ".json":
                data = json.loads(content)
            else:
                data = yaml.safe_load(content)
            
            if isinstance(data, dict):
                paths = data.get("paths", {})
                for path in paths.keys():
                    items.append(f"Path: {path}")
                
                schemas = data.get("components", {}).get("schemas", {})
                for schema_name in schemas.keys():
                    items.append(f"Schema: {schema_name}")
        except Exception:
            pass
        
        return items

    def _parse_ts_api_def(self, content: str) -> List[str]:
        """解析 TypeScript API 定义"""
        items = []
        
        patterns = [
            r'(?:export\s+)?(?:const|function)\s+(\w+)\s*[:=].*[\"\']/api',
            r'(?:interface|type)\s+(\w+[Rr]equest\w*|Api\w+)',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                items.append(f"API: {match.group(1)}")
        
        return items

    def _parse_python_models(self, content: str) -> List[str]:
        """解析 Python 模型定义"""
        models = []
        
        patterns = [
            r'@dataclass\s+class\s+(\w+)',
            r'^class\s+(\w+(?:Model|DTO|Schema|Request|Response)?)\s*[:\(]',
            r'class\s+(\w+(?:Model|DTO|Schema|Request|Response)?)\s*[:\(]',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.MULTILINE):
                class_name = match.group(1)
                model_entry = f"Class: {class_name}"
                if model_entry not in models:
                    models.append(model_entry)
        
        return models

    def _parse_go_models(self, content: str) -> List[str]:
        """解析 Go 模型定义"""
        models = []
        
        pattern = r'type\s+(\w+(?:Model|DTO|Request|Response)?)\s+struct'
        
        for match in re.finditer(pattern, content):
            models.append(f"Struct: {match.group(1)}")
        
        return models

    def _parse_ts_models(self, content: str) -> List[str]:
        """解析 TypeScript 模型定义"""
        models = []
        
        patterns = [
            r'(?:export\s+)?(?:interface|type)\s+(\w+(?:Model|DTO|Request|Response)?)\s*[<={]',
            r'(?:export\s+)?class\s+(\w+(?:Model|DTO|Request|Response)?)\s*[<{]',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                models.append(f"Type: {match.group(1)}")
        
        return models

    def _is_breaking_change(self, commit_message: str) -> bool:
        """检查是否为破坏性变更"""
        keywords = [
            "break", "breaking", "breaking change",
            "incompatible", "deprecated",
            "remove", "delete", "drop",
            "!!!", "BREAKING",
        ]
        
        msg_lower = commit_message.lower()
        return any(kw in msg_lower for kw in keywords)

    def detect_all_changes(
        self,
        scan_results: List[RepoScanResult],
        repo_paths: Dict[str, str]
    ) -> Dict[str, ChangeAnalysis]:
        """批量检测所有仓库的变更"""
        all_analysis: Dict[str, ChangeAnalysis] = {}
        
        for result in scan_results:
            if result.has_changes:
                repo_path = repo_paths.get(result.repo_name, "")
                analysis = self.detect_changes(result, repo_path)
                all_analysis[result.repo_name] = analysis
        
        return all_analysis
