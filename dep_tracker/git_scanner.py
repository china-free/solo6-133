"""
Git 仓库扫描模块
负责并发扫描多个 Git 仓库，查找带有指定 Issue ID 的提交
"""

import fnmatch
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from git import GitCommandError, InvalidGitRepositoryError, Repo

from .config import RepositoryConfig


@dataclass
class CommitInfo:
    """提交信息"""
    repo_name: str
    commit_hash: str
    short_hash: str
    message: str
    author: str
    date: datetime
    changed_files: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"[{self.repo_name}] {self.short_hash} - {self.message[:50]}..."


@dataclass
class RepoScanResult:
    """仓库扫描结果"""
    repo_name: str
    repo_path: str
    repo_type: str
    commits: List[CommitInfo] = field(default_factory=list)
    error: Optional[str] = None
    api_contract_changes: List[str] = field(default_factory=list)
    shared_model_changes: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """是否有变更"""
        return len(self.commits) > 0


class GitScanner:
    """Git 仓库扫描器"""

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self._repo_cache: Dict[str, Repo] = {}

    def _get_repo(self, repo_path: str) -> Optional[Repo]:
        """获取 Git 仓库实例（带缓存）"""
        if repo_path in self._repo_cache:
            return self._repo_cache[repo_path]

        try:
            path = Path(repo_path).resolve()
            if not path.exists():
                return None
            repo = Repo(str(path))
            self._repo_cache[repo_path] = repo
            return repo
        except (InvalidGitRepositoryError, Exception):
            return None

    def _match_issue_id(self, message: str, issue_id: str) -> bool:
        """检查提交消息是否包含 Issue ID"""
        pattern = re.compile(rf'\b{re.escape(issue_id)}\b', re.IGNORECASE)
        return bool(pattern.search(message))

    def _match_file_patterns(self, file_path: str, patterns: List[str]) -> bool:
        """检查文件路径是否匹配任意模式"""
        path_obj = Path(file_path)
        
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(path_obj.name, pattern):
                return True
            
            if '**' in pattern:
                import re
                regex_pattern = pattern.replace('**', '.*').replace('*', '[^/]*').replace('?', '.')
                if re.match(f'^{regex_pattern}$', file_path):
                    return True
                
                if '**/' in pattern:
                    prefix, rest = pattern.split('**/', 1)
                    if file_path.startswith(prefix):
                        remaining = file_path[len(prefix):]
                        if fnmatch.fnmatch(remaining, rest) or fnmatch.fnmatch(path_obj.name, rest):
                            return True
                
                parts = pattern.split('**')
                if len(parts) >= 2:
                    suffix = parts[-1]
                    if fnmatch.fnmatch(path_obj.name, suffix.lstrip('/')):
                        if parts[0] and file_path.startswith(parts[0]):
                            return True
        return False

    def _get_changed_files(self, commit) -> List[str]:
        """获取提交修改的文件列表"""
        try:
            if commit.parents:
                diff = commit.parents[0].diff(commit)
            else:
                diff = commit.diff()
            return [item.a_path for item in diff]
        except Exception:
            return []

    def scan_repository(
        self,
        repo_config: RepositoryConfig,
        issue_id: str,
        max_commits: int = 100
    ) -> RepoScanResult:
        """扫描单个仓库"""
        result = RepoScanResult(
            repo_name=repo_config.name,
            repo_path=repo_config.path,
            repo_type=repo_config.type,
        )

        repo = self._get_repo(repo_config.path)
        if not repo:
            result.error = f"不是有效的 Git 仓库或路径不存在: {repo_config.path}"
            return result

        try:
            matching_commits = []
            
            for commit in repo.iter_commits(max_count=max_commits):
                if self._match_issue_id(commit.message, issue_id):
                    changed_files = self._get_changed_files(commit)
                    
                    commit_info = CommitInfo(
                        repo_name=repo_config.name,
                        commit_hash=commit.hexsha,
                        short_hash=commit.hexsha[:7],
                        message=commit.message.strip(),
                        author=f"{commit.author.name} <{commit.author.email}>",
                        date=datetime.fromtimestamp(commit.committed_date),
                        changed_files=changed_files,
                    )
                    matching_commits.append(commit_info)

                    for file_path in changed_files:
                        if self._match_file_patterns(file_path, repo_config.api_contract_patterns):
                            if file_path not in result.api_contract_changes:
                                result.api_contract_changes.append(file_path)
                        if self._match_file_patterns(file_path, repo_config.shared_model_patterns):
                            if file_path not in result.shared_model_changes:
                                result.shared_model_changes.append(file_path)

            result.commits = matching_commits

        except GitCommandError as e:
            result.error = f"Git 命令执行失败: {str(e)}"
        except Exception as e:
            result.error = f"扫描出错: {str(e)}"

        return result

    def scan_repositories(
        self,
        repo_configs: List[RepositoryConfig],
        issue_id: str,
        max_commits: int = 100
    ) -> List[RepoScanResult]:
        """并发扫描多个仓库"""
        results: List[RepoScanResult] = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {
                executor.submit(
                    self.scan_repository,
                    repo_config,
                    issue_id,
                    max_commits
                ): repo_config
                for repo_config in repo_configs
            }

            for future in as_completed(future_to_repo):
                repo_config = future_to_repo[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(RepoScanResult(
                        repo_name=repo_config.name,
                        repo_path=repo_config.path,
                        repo_type=repo_config.type,
                        error=f"执行异常: {str(e)}",
                    ))

        results.sort(key=lambda r: r.repo_name)
        return results

    def close(self):
        """关闭资源"""
        for repo in self._repo_cache.values():
            try:
                repo.close()
            except Exception:
                pass
        self._repo_cache.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
