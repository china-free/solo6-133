#!/usr/bin/env python3
"""
单元测试
验证跨仓库变更追踪工具的核心功能
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from dep_tracker.config import ConfigLoader, RepositoryConfig
from dep_tracker.git_scanner import CommitInfo, GitScanner, RepoScanResult
from dep_tracker.change_detector import ChangeDetector, ContractChange, ModelChange
from dep_tracker.dependency_analyzer import (
    DependencyAnalyzer,
    DependencyEdge,
    RepositoryNode,
    TopologyGraph,
)
from dep_tracker.visualizer import DisplayConfig, TerminalVisualizer


class TestConfigLoader(unittest.TestCase):
    """配置加载器测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "test_config.yaml"
        self.config_content = """
repositories:
  - name: repo1
    path: ./repo1
    type: backend
    api_contract_patterns:
      - "*.proto"
    shared_model_patterns:
      - "models/**/*.py"

  - name: repo2
    path: ./repo2
    type: frontend
    api_contract_patterns:
      - "src/api/**/*.ts"

dependency_rules:
  - from: repo1
    to: repo2
    reason: "前端依赖后端API"
"""
        self.config_path.write_text(self.config_content, encoding='utf-8')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_config(self):
        """测试加载配置"""
        config = ConfigLoader.load(str(self.config_path))
        
        self.assertEqual(len(config.repositories), 2)
        self.assertEqual(len(config.dependency_rules), 1)
        
        repo1 = config.get_repo("repo1")
        self.assertIsNotNone(repo1)
        self.assertEqual(repo1.name, "repo1")
        self.assertEqual(repo1.type, "backend")
        self.assertEqual(repo1.api_contract_patterns, ["*.proto"])
        
        rule = config.dependency_rules[0]
        self.assertEqual(rule.from_repo, "repo1")
        self.assertEqual(rule.to_repo, "repo2")
        self.assertEqual(rule.reason, "前端依赖后端API")

    def test_path_resolution(self):
        """测试路径解析"""
        config = ConfigLoader.load(str(self.config_path))
        repo1 = config.get_repo("repo1")
        
        self.assertTrue(Path(repo1.path).is_absolute())
        self.assertTrue(repo1.path.endswith("repo1"))

    def test_find_default_config(self):
        """测试查找默认配置"""
        import shutil
        cwd_config = Path.cwd() / "config.yaml"
        temp_rename = Path.cwd() / "config.yaml.temp"
        
        if cwd_config.exists():
            shutil.move(str(cwd_config), str(temp_rename))
        
        try:
            default_path = ConfigLoader.find_default_config()
            self.assertIsNone(default_path)
        finally:
            if temp_rename.exists():
                shutil.move(str(temp_rename), str(cwd_config))


class TestGitScanner(unittest.TestCase):
    """Git 扫描器测试"""

    def test_issue_id_matching(self):
        """测试 Issue ID 匹配"""
        scanner = GitScanner()
        
        self.assertTrue(scanner._match_issue_id("Fix bug JIRA-1024", "JIRA-1024"))
        self.assertTrue(scanner._match_issue_id("[JIRA-1024] Add feature", "JIRA-1024"))
        self.assertTrue(scanner._match_issue_id("jira-1024 fix", "JIRA-1024"))
        self.assertFalse(scanner._match_issue_id("Fix bug JIRA-10245", "JIRA-1024"))
        self.assertFalse(scanner._match_issue_id("Fix bug", "JIRA-1024"))

    def test_file_pattern_matching(self):
        """测试文件模式匹配"""
        scanner = GitScanner()
        
        patterns = ["*.proto", "api/**/*.yaml", "models/**/*.py"]
        
        self.assertTrue(scanner._match_file_patterns("user.proto", patterns))
        self.assertTrue(scanner._match_file_patterns("api/v1/openapi.yaml", patterns))
        self.assertTrue(scanner._match_file_patterns("models/user.py", patterns))
        self.assertFalse(scanner._match_file_patterns("README.md", patterns))
        self.assertFalse(scanner._match_file_patterns("main.py", patterns))


class TestChangeDetector(unittest.TestCase):
    """变更检测器测试"""

    def setUp(self):
        self.detector = ChangeDetector()

    def test_is_breaking_change(self):
        """测试破坏性变更检测"""
        self.assertTrue(self.detector._is_breaking_change("feat: new API\nBREAKING CHANGE: remove old API"))
        self.assertTrue(self.detector._is_breaking_change("fix: remove deprecated field !!!"))
        self.assertTrue(self.detector._is_breaking_change("refactor: breaking change to status field"))
        self.assertFalse(self.detector._is_breaking_change("feat: add new optional field"))
        self.assertFalse(self.detector._is_breaking_change("fix: typo in documentation"))

    def test_parse_proto_contract(self):
        """测试 Protobuf 契约解析"""
        content = """
syntax = "proto3";

service UserService {
  rpc GetUser(UserRequest) returns (UserResponse);
  rpc UpdateUser(UserUpdateRequest) returns (UserResponse);
}

message UserRequest {
  string id = 1;
}
"""
        items = self.detector._parse_proto_contract(content)
        self.assertIn("Service: UserService", items)
        self.assertIn("RPC: GetUser", items)
        self.assertIn("RPC: UpdateUser", items)

    def test_parse_proto_models(self):
        """测试 Protobuf 模型解析"""
        content = """
message UserRequest {
  string id = 1;
}

message UserResponse {
  string id = 1;
  string name = 2;
}
"""
        models = self.detector._parse_proto_models(content)
        self.assertIn("Message: UserRequest", models)
        self.assertIn("Message: UserResponse", models)

    def test_parse_python_models(self):
        """测试 Python 模型解析"""
        content = """
@dataclass
class UserModel:
    id: str
    name: str

class UserDTO:
    id: str
    name: str
"""
        models = self.detector._parse_python_models(content)
        self.assertIn("Class: UserModel", models)
        self.assertIn("Class: UserDTO", models)

    def test_parse_ts_models(self):
        """测试 TypeScript 模型解析"""
        content = """
export interface UserModel {
  id: string;
  name: string;
}

export type UserStatus = 'active' | 'inactive';

export class UserViewModel {
  id: string;
  displayName: string;
}
"""
        models = self.detector._parse_ts_models(content)
        self.assertIn("Type: UserModel", models)
        self.assertIn("Type: UserStatus", models)
        self.assertIn("Type: UserViewModel", models)


class TestDependencyAnalyzer(unittest.TestCase):
    """依赖分析器测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "test_config.yaml"
        self.config_content = """
repositories:
  - name: user-service
    path: ./user-service
    type: backend
  - name: order-service
    path: ./order-service
    type: backend
  - name: web-frontend
    path: ./web-frontend
    type: frontend

dependency_rules:
  - from: user-service
    to: order-service
    reason: "订单服务依赖用户服务"
  - from: user-service
    to: web-frontend
    reason: "前端依赖用户服务"
  - from: order-service
    to: web-frontend
    reason: "前端依赖订单服务"
"""
        self.config_path.write_text(self.config_content, encoding='utf-8')
        self.config = ConfigLoader.load(str(self.config_path))
        self.analyzer = DependencyAnalyzer(self.config)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_build_topology(self):
        """测试构建拓扑图"""
        from dep_tracker.change_detector import ChangeAnalysis
        
        change_analysis = {
            "user-service": ChangeAnalysis(
                repo_name="user-service",
                commits=[MagicMock()],
            ),
            "order-service": ChangeAnalysis(
                repo_name="order-service",
                commits=[MagicMock()],
            ),
            "web-frontend": ChangeAnalysis(
                repo_name="web-frontend",
                commits=[MagicMock()],
            ),
        }
        
        topology = self.analyzer._build_topology(change_analysis)
        
        self.assertEqual(len(topology.nodes), 3)
        self.assertIn("user-service", topology.nodes)
        self.assertIn("order-service", topology.nodes)
        self.assertIn("web-frontend", topology.nodes)
        
        user_node = topology.nodes["user-service"]
        self.assertTrue(user_node.has_changes)
        
        edge = topology.get_edge("user-service", "order-service")
        self.assertIsNotNone(edge)
        self.assertEqual(edge.reason, "订单服务依赖用户服务")

    def test_multilevel_topo_sort(self):
        """测试多层拓扑排序"""
        import networkx as nx
        
        graph = nx.DiGraph()
        graph.add_node("user-service")
        graph.add_node("order-service")
        graph.add_node("web-frontend")
        graph.add_edge("user-service", "order-service")
        graph.add_edge("user-service", "web-frontend")
        graph.add_edge("order-service", "web-frontend")
        
        levels = self.analyzer._multilevel_topo_sort(graph)
        
        self.assertEqual(len(levels), 3)
        self.assertEqual(levels[0], ["user-service"])
        self.assertEqual(levels[1], ["order-service"])
        self.assertEqual(levels[2], ["web-frontend"])

    def test_calculate_release_order(self):
        """测试计算发布顺序"""
        from dep_tracker.change_detector import ChangeAnalysis
        
        change_analysis = {
            "user-service": ChangeAnalysis(
                repo_name="user-service",
                commits=[MagicMock()],
            ),
            "order-service": ChangeAnalysis(
                repo_name="order-service",
                commits=[MagicMock()],
            ),
            "web-frontend": ChangeAnalysis(
                repo_name="web-frontend",
                commits=[MagicMock()],
            ),
        }
        
        topology = self.analyzer._build_topology(change_analysis)
        release_order = self.analyzer._calculate_release_order(topology, change_analysis)
        
        self.assertEqual(len(release_order), 3)
        self.assertEqual(release_order[0].repo_names, ["user-service"])
        self.assertEqual(release_order[1].repo_names, ["order-service"])
        self.assertEqual(release_order[2].repo_names, ["web-frontend"])


class TestTerminalVisualizer(unittest.TestCase):
    """终端可视化器测试"""

    def setUp(self):
        self.visualizer = TerminalVisualizer(DisplayConfig(use_color=False))

    def test_format_repo_name(self):
        """测试格式化仓库名称"""
        node = RepositoryNode(
            name="user-service",
            repo_type="backend",
            has_changes=True,
            commit_count=3,
        )
        
        display = self.visualizer._format_repo_name(node)
        self.assertIn("user-service", display)
        self.assertIn("✓", display)

    def test_format_breaking_change(self):
        """测试格式化破坏性变更"""
        node = RepositoryNode(
            name="user-service",
            repo_type="backend",
            has_changes=True,
            has_breaking_changes=True,
            commit_count=3,
        )
        
        display = self.visualizer._format_repo_name(node)
        self.assertIn("user-service", display)
        self.assertIn("⚠️", display)

    def test_render_header(self):
        """测试渲染标题"""
        header = self.visualizer.render_header("JIRA-1024")
        self.assertIn("JIRA-1024", header)
        self.assertIn("跨仓库变更依赖分析", header)


def run_tests():
    """运行所有测试"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
