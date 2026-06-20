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
        """获取提交修改的文件列表
        
        基于 commit 的 tree object 进行分析，不依赖当前工作树。
        能够正确处理跨分支、跨提交的所有类型的文件变更。
        """
        changed_files: List[str] = []
        
        try:
            if commit.parents:
                for parent in commit.parents:
                    try:
                        diff_index = parent.diff(commit)
                        for diff_item in diff_index:
                            file_path = self._resolve_diff_item_path(diff_item)
                            if file_path and file_path not in changed_files:
                                changed_files.append(file_path)
                    except Exception:
                        continue
            else:
                changed_files = self._list_all_files_from_tree(commit.tree)
        except Exception:
            try:
                changed_files = self._get_changed_files_via_git_cmd(commit)
            except Exception:
                pass
        
        return list(set(changed_files))

    def _resolve_diff_item_path(self, diff_item) -> Optional[str]:
        """解析 diff 项中的文件路径，处理各种变更类型"""
        try:
            change_type = diff_item.change_type
            
            if change_type in ('A', 'M', 'T'):
                return diff_item.b_path if diff_item.b_path else diff_item.a_path
            
            if change_type in ('D',):
                return diff_item.a_path if diff_item.a_path else diff_item.b_path
            
            if change_type in ('R',):
                return diff_item.b_path if diff_item.b_path else diff_item.a_path
            
            if change_type in ('C',):
                return diff_item.b_path if diff_item.b_path else diff_item.a_path
            
            if diff_item.b_path:
                return diff_item.b_path
            if diff_item.a_path:
                return diff_item.a_path
        except Exception:
            try:
                return diff_item.b_path or diff_item.a_path
            except Exception:
                return None
        return None

    def _list_all_files_from_tree(self, tree) -> List[str]:
        """从 git tree 对象中递归列出所有文件"""
        files = []
        
        try:
            for item in tree.traverse():
                try:
                    if item.type == 'blob':
                        files.append(item.path)
                except Exception:
                    continue
        except Exception:
            pass
        
        return files

    def _get_changed_files_via_git_cmd(self, commit) -> List[str]:
        """通过 git 命令行获取变更文件（后备方案）"""
        files = []
        
        try:
            repo = commit.repo
            commit_hash = commit.hexsha
            
            if commit.parents:
                parent_hash = commit.parents[0].hexsha
                result = repo.git.diff(
                    '--name-only', '--no-renames',
                    f'{parent_hash}..{commit_hash}'
                )
            else:
                result = repo.git.show(
                    '--pretty=format:', '--name-only', '--no-renames',
                    commit_hash
                )
            
            if result:
                for line in result.strip().split('\n'):
                    line = line.strip()
                    if line and line not in files:
                        files.append(line)
        except Exception:
            pass
        
        return files

    def scan_repository(
        self,
        repo_config: RepositoryConfig,
        issue_id: str,
        max_commits: int = 100
    ) -> RepoScanResult:
        """扫描单个仓库（包括所有分支）"""
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
            matching_commits: Dict[str, CommitInfo] = {}
            
            all_refs = self._get_all_commit_refs(repo)
            
            for ref in all_refs:
                try:
                    commit_iter = repo.iter_commits(ref, max_count=max_commits)
                    for commit in commit_iter:
                        if commit.hexsha in matching_commits:
                            continue
                        
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
                            matching_commits[commit.hexsha] = commit_info

                            for file_path in changed_files:
                                if self._match_file_patterns(file_path, repo_config.api_contract_patterns):
                                    if file_path not in result.api_contract_changes:
                                        result.api_contract_changes.append(file_path)
                                if self._match_file_patterns(file_path, repo_config.shared_model_patterns):
                                    if file_path not in result.shared_model_changes:
                                        result.shared_model_changes.append(file_path)
                except Exception:
                    continue

            result.commits = list(matching_commits.values())
            result.commits.sort(key=lambda c: c.date, reverse=True)

        except GitCommandError as e:
            result.error = f"Git 命令执行失败: {str(e)}"
        except Exception as e:
            result.error = f"扫描出错: {str(e)}"

        return result

    def _get_all_commit_refs(self, repo) -> List[str]:
        """获取所有需要扫描的 refs（分支、标签等）"""
        refs = []
        
        try:
            refs.append('HEAD')
        except Exception:
            pass
        
        try:
            for branch in repo.branches:
                try:
                    ref_name = branch.name
                    if ref_name not in refs:
                        refs.append(ref_name)
                except Exception:
                    continue
        except Exception:
            pass
        
        try:
            remote_refs = []
            for remote in repo.remotes:
                try:
                    for ref in remote.refs:
                        try:
                            remote_refs.append(ref.name)
                        except Exception:
                            continue
                except Exception:
                    continue
            
            for ref in remote_refs:
                if ref not in refs:
                    refs.append(ref)
        except Exception:
            pass
        
        try:
            for tag in repo.tags:
                try:
                    tag_ref = tag.name
                    if tag_ref not in refs:
                        refs.append(tag_ref)
                except Exception:
                    continue
        except Exception:
            pass
        
        if not refs:
            refs = ['--all']
        
        return refs

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

    def get_all_repos(self) -> Dict[str, Repo]:
        """获取所有仓库的 Git Repo 对象
        
        Returns:
            仓库名称到 GitPython Repo 对象的映射
        """
        return dict(self._repo_cache)

    def get_repo_by_path(self, repo_path: str) -> Optional[Repo]:
        """根据路径获取仓库对象
        
        Args:
            repo_path: 仓库路径
        
        Returns:
            GitPython Repo 对象，如果不存在返回 None
        """
        return self._repo_cache.get(repo_path)

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
