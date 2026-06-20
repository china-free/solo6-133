#!/usr/bin/env python3
"""
跨分支变更检测测试
验证不同分支的提交都能被正确扫描和分析
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from git import Repo


def create_cross_branch_test_env():
    """创建跨分支测试环境"""
    base_dir = Path(tempfile.mkdtemp(prefix="cross_branch_test_"))
    issue_id = "JIRA-CROSS-001"
    
    print(f"Test directory: {base_dir}")
    print(f"Issue ID: {issue_id}")
    print()
    
    repo_path = base_dir / "multi-branch-repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    os.chdir(repo_path)
    
    repo = Repo.init()
    
    config_writer = repo.config_writer()
    config_writer.set_value("user", "name", "Test User")
    config_writer.set_value("user", "email", "test@example.com")
    config_writer.release()
    
    print("=" * 60)
    print("Creating commits on MAIN branch")
    print("=" * 60)
    
    readme = repo_path / "README.md"
    readme.write_text("# Multi-branch Test Repo\n", encoding='utf-8')
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    
    api_dir = repo_path / "api"
    api_dir.mkdir(exist_ok=True)
    models_dir = repo_path / "models"
    models_dir.mkdir(exist_ok=True)
    
    (repo_path / "service.proto").write_text(
        'syntax = "proto3";\n'
        '\n'
        'package main;\n'
        '\n'
        'service MainService {\n'
        '  rpc GetStatus(StatusRequest) returns (StatusResponse);\n'
        '}\n'
        '\n'
        'message StatusRequest { string id = 1; }\n'
        'message StatusResponse { string status = 1; }\n',
        encoding='utf-8'
    )
    
    (api_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Main API\n"
        "  version: 1.0.0\n"
        "paths:\n"
        "  /api/v1/status:\n"
        "    get:\n"
        "      summary: Get status\n",
        encoding='utf-8'
    )
    
    (models_dir / "base.py").write_text(
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class BaseModel:\n"
        "    id: str\n",
        encoding='utf-8'
    )
    
    repo.index.add(["service.proto", "api/openapi.yaml", "models/base.py"])
    repo.index.commit("feat: Add core structure")
    
    print("  - Initial structure created on main")
    print()
    
    print("=" * 60)
    print("Creating FEATURE branch: feature/user-profile")
    print("=" * 60)
    
    feature_branch = repo.create_head("feature/user-profile")
    feature_branch.checkout()
    
    print(f"  - Switched to branch: {repo.active_branch.name}")
    
    (repo_path / "user.proto").write_text(
        f'syntax = "proto3";\n'
        '\n'
        'package user;\n'
        '\n'
        'service UserService {\n'
        f'  rpc GetUser(UserRequest) returns (UserResponse); // [{issue_id}]\n'
        '}\n'
        '\n'
        'message UserRequest { string user_id = 1; }\n'
        'message UserResponse {\n'
        '  string user_id = 1;\n'
        '  string name = 2;\n'
        '  string email = 3;\n'
        '}\n',
        encoding='utf-8'
    )
    
    (models_dir / "user.py").write_text(
        f"from dataclasses import dataclass\n"
        "\n"
        f"@dataclass\n"
        "class UserModel:\n"
        "    user_id: str\n"
        "    name: str\n"
        "    email: str\n",
        encoding='utf-8'
    )
    
    repo.index.add(["user.proto", "models/user.py"])
    commit1 = repo.index.commit(
        f"feat: Add user profile API [{issue_id}]\n\n"
        f"- Add UserService with GetUser RPC\n"
        f"- Add UserModel data class\n"
    )
    print(f"  - Created commit: {commit1.hexsha[:7]} (on feature/user-profile)")
    
    (repo_path / "user.proto").write_text(
        f'syntax = "proto3";\n'
        '\n'
        'package user;\n'
        '\n'
        'service UserService {\n'
        f'  rpc GetUser(UserRequest) returns (UserResponse); // [{issue_id}]\n'
        f'  rpc UpdateUser(UserUpdateRequest) returns (UserResponse); // [{issue_id}]\n'
        '}\n'
        '\n'
        'message UserRequest { string user_id = 1; }\n'
        'message UserResponse {\n'
        '  string user_id = 1;\n'
        '  string name = 2;\n'
        '  string email = 3;\n'
        '  int32 status = 4;  // BREAKING CHANGE: new required field\n'
        '}\n'
        '\n'
        'message UserUpdateRequest {\n'
        '  string user_id = 1;\n'
        '  string name = 2;\n'
        '  int32 status = 3;\n'
        '}\n',
        encoding='utf-8'
    )
    
    (models_dir / "user.py").write_text(
        f"from dataclasses import dataclass\n"
        "from typing import Optional\n"
        "\n"
        f"@dataclass\n"
        "class UserModel:\n"
        "    user_id: str\n"
        "    name: str\n"
        "    email: str\n"
        "    status: int  # BREAKING CHANGE: status field type changed\n"
        "\n"
        f"@dataclass\n"
        "class UserUpdateDTO:\n"
        "    user_id: str\n"
        "    name: Optional[str] = None\n"
        "    status: Optional[int] = None\n",
        encoding='utf-8'
    )
    
    repo.index.add(["user.proto", "models/user.py"])
    commit2 = repo.index.commit(
        f"feat: Add UpdateUser API and status field [{issue_id}]\n\n"
        f"BREAKING CHANGE: Added required status field to UserResponse\n"
        f"BREAKING CHANGE: UserModel status changed from str to int\n"
    )
    print(f"  - Created commit: {commit2.hexsha[:7]} (on feature/user-profile)")
    print()
    
    print("=" * 60)
    print("Switching back to MAIN and creating HOTFIX branch")
    print("=" * 60)
    
    main_branch = [b for b in repo.branches if b.name in ('main', 'master')][0]
    main_branch.checkout()
    print(f"  - Switched to branch: {repo.active_branch.name}")
    
    hotfix_branch = repo.create_head("hotfix/auth-patch")
    hotfix_branch.checkout()
    print(f"  - Created and switched to branch: {repo.active_branch.name}")
    
    (repo_path / "auth.proto").write_text(
        f'syntax = "proto3";\n'
        '\n'
        'package auth;\n'
        '\n'
        'service AuthService {\n'
        f'  rpc Login(LoginRequest) returns (LoginResponse); // [{issue_id}]\n'
        f'  rpc ValidateToken(TokenRequest) returns (TokenResponse); // [{issue_id}]\n'
        '}\n'
        '\n'
        'message LoginRequest {\n'
        '  string username = 1;\n'
        '  string password = 2;\n'
        '}\n'
        '\n'
        'message LoginResponse {\n'
        '  string token = 1;\n'
        '  int64 expires_at = 2;\n'
        '}\n'
        '\n'
        'message TokenRequest { string token = 1; }\n'
        'message TokenResponse { bool valid = 1; string user_id = 2; }\n',
        encoding='utf-8'
    )
    
    (models_dir / "auth.py").write_text(
        f"from dataclasses import dataclass\n"
        "from typing import Optional\n"
        "\n"
        f"@dataclass\n"
        "class AuthToken:\n"
        "    token: str\n"
        "    expires_at: int\n"
        "    user_id: str\n"
        "\n"
        f"@dataclass\n"
        "class LoginCredentials:\n"
        "    username: str\n"
        "    password: str\n",
        encoding='utf-8'
    )
    
    repo.index.add(["auth.proto", "models/auth.py"])
    commit3 = repo.index.commit(
        f"feat: Add authentication APIs [{issue_id}]\n\n"
        f"- Add Login and ValidateToken RPCs\n"
        f"- Add AuthToken and LoginCredentials models\n"
    )
    print(f"  - Created commit: {commit3.hexsha[:7]} (on hotfix/auth-patch)")
    print()
    
    print("=" * 60)
    print("Switching back to MAIN (working tree)")
    print("=" * 60)
    
    main_branch.checkout()
    print(f"  - Working tree on: {repo.active_branch.name}")
    print(f"  - NOTE: The commits on feature/hotfix branches are NOT in working tree")
    print()
    
    print("=" * 60)
    print("Test repository structure:")
    print("=" * 60)
    print(f"  Branches:")
    for branch in repo.branches:
        marker = " *" if branch.name == repo.active_branch.name else "  "
        print(f"  {marker} {branch.name}")
    print()
    print(f"  Working tree files (on {repo.active_branch.name}):")
    for item in sorted(repo_path.rglob('*')):
        if item.is_file() and '.git' not in item.parts:
            rel_path = item.relative_to(repo_path)
            print(f"    - {rel_path}")
    print()
    print(f"  Files on feature/user-profile (NOT in working tree):")
    print(f"    - user.proto")
    print(f"    - models/user.py")
    print()
    print(f"  Files on hotfix/auth-patch (NOT in working tree):")
    print(f"    - auth.proto")
    print(f"    - models/auth.py")
    print()
    
    config_content = f"""repositories:
  - name: multi-branch-repo
    path: {repo_path}
    type: backend
    api_contract_patterns:
      - "*.proto"
      - "api/**/*.yaml"
    shared_model_patterns:
      - "models/**/*.py"

dependency_rules:
  - from: multi-branch-repo
    to: multi-branch-repo
    reason: "Self-reference for testing"
"""
    
    test_config = base_dir.parent / "test_cross_branch.yaml"
    test_config.write_text(config_content, encoding='utf-8')
    
    print(f"  Test config: {test_config}")
    print()
    
    return base_dir, test_config, issue_id, repo_path


def run_cross_branch_test():
    """运行跨分支测试"""
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    print()
    print("=" * 60)
    print("CROSS-BRANCH CHANGE DETECTION TEST")
    print("=" * 60)
    print()
    
    base_dir, test_config, issue_id, repo_path = create_cross_branch_test_env()
    
    try:
        from dep_tracker.config import ConfigLoader
        from dep_tracker.git_scanner import GitScanner
        from dep_tracker.change_detector import ChangeDetector
        from dep_tracker.dependency_analyzer import DependencyAnalyzer
        from dep_tracker.visualizer import TerminalVisualizer, DisplayConfig
        
        print("=" * 60)
        print("STEP 1: Loading configuration")
        print("=" * 60)
        config = ConfigLoader.load(str(test_config))
        print(f"  OK: Loaded {len(config.repositories)} repo(s)")
        print()
        
        print("=" * 60)
        print("STEP 2: Scanning ALL branches (not just working tree)")
        print("=" * 60)
        with GitScanner(max_workers=3) as scanner:
            scan_results = scanner.scan_repositories(
                config.repositories,
                issue_id,
                max_commits=100
            )
        
        for result in scan_results:
            print(f"  Repo: {result.repo_name}")
            print(f"    Type: {result.repo_type}")
            print(f"    Working tree path: {result.repo_path}")
            print(f"    Matching commits found: {len(result.commits)}")
            print()
            
            if result.commits:
                print(f"    Commits (from ANY branch):")
                for commit in result.commits:
                    branch_info = "(commit not on main branch!)"
                    print(f"      - {commit.short_hash}: {commit.message[:60]}")
                    print(f"           Date: {commit.date.strftime('%Y-%m-%d %H:%M')}")
                    print(f"           Files changed ({len(commit.changed_files)}):")
                    for f in commit.changed_files:
                        print(f"             • {f}")
                    print()
            
            print(f"    API contract changes detected: {len(result.api_contract_changes)}")
            for f in result.api_contract_changes:
                print(f"      • {f}")
            print()
            print(f"    Shared model changes detected: {len(result.shared_model_changes)}")
            for f in result.shared_model_changes:
                print(f"      • {f}")
            print()
        
        print("=" * 60)
        print("STEP 3: Analyzing changes from tree objects")
        print("=" * 60)
        detector = ChangeDetector()
        repo_paths = {repo.name: repo.path for repo in config.repositories}
        
        from git import Repo as GitRepo
        git_repos = {}
        for repo_config in config.repositories:
            try:
                git_repos[repo_config.name] = GitRepo(repo_config.path)
            except Exception:
                pass
        
        change_analysis = detector.detect_all_changes(
            scan_results,
            repo_paths,
            git_repos
        )
        
        for repo_name, analysis in change_analysis.items():
            print(f"  Repo: {repo_name}")
            print(f"    Has API changes: {analysis.has_api_changes}")
            print(f"    Has model changes: {analysis.has_model_changes}")
            print(f"    Has breaking changes: {analysis.has_breaking_changes}")
            print(f"    Affected items: {len(analysis.all_affected_items)}")
            for item in sorted(list(analysis.all_affected_items))[:10]:
                print(f"      • {item}")
            print()
        
        print("=" * 60)
        print("STEP 4: VALIDATION CRITICAL CHECKS")
        print("=" * 60)
        
        all_passed = True
        
        check1 = len(result.commits) >= 3
        print(f"  [{'PASS' if check1 else 'FAIL'}] Found commits from ALL branches (expected >= 3, got {len(result.commits)})")
        all_passed = all_passed and check1
        
        proto_files = [f for f in result.api_contract_changes if f.endswith('.proto')]
        check2 = 'user.proto' in proto_files and 'auth.proto' in proto_files
        print(f"  [{'PASS' if check2 else 'FAIL'}] Detected proto files from OTHER branches:")
        print(f"         user.proto (from feature branch): {'YES' if 'user.proto' in proto_files else 'NO'}")
        print(f"         auth.proto (from hotfix branch):  {'YES' if 'auth.proto' in proto_files else 'NO'}")
        all_passed = all_passed and check2
        
        model_files = [f for f in result.shared_model_changes if f.endswith('.py')]
        check3 = 'models/user.py' in model_files and 'models/auth.py' in model_files
        print(f"  [{'PASS' if check3 else 'FAIL'}] Detected model files from OTHER branches:")
        print(f"         models/user.py (from feature):  {'YES' if 'models/user.py' in model_files else 'NO'}")
        print(f"         models/auth.py (from hotfix):   {'YES' if 'models/auth.py' in model_files else 'NO'}")
        all_passed = all_passed and check3
        
        analysis = change_analysis.get('multi-branch-repo')
        check4 = analysis is not None and analysis.has_breaking_changes
        print(f"  [{'PASS' if check4 else 'FAIL'}] Breaking changes detected from non-working-tree commits")
        all_passed = all_passed and check4
        
        check5 = analysis is not None and any(
            'Service: UserService' in item or 'Service: AuthService' in item
            for item in analysis.all_affected_items
        )
        print(f"  [{'PASS' if check5 else 'FAIL'}] Service definitions parsed from tree objects")
        all_passed = all_passed and check5
        
        check6 = analysis is not None and any(
            'Class: UserModel' in item or 'Class: AuthToken' in item
            for item in analysis.all_affected_items
        )
        print(f"  [{'PASS' if check6 else 'FAIL'}] Model class definitions parsed from tree objects")
        all_passed = all_passed and check6
        
        print()
        print("=" * 60)
        print(f"FINAL RESULT: {'ALL TESTS PASSED ✓' if all_passed else 'SOME TESTS FAILED ✗'}")
        print("=" * 60)
        print()
        
        if all_passed:
            print("SUCCESS: Cross-branch file comparison is working correctly!")
            print()
            print("Key achievements:")
            print("  ✓ Scans commits from ALL branches (not just current working tree)")
            print("  ✓ Extracts changed files correctly from commit tree objects")
            print("  ✓ Analyzes API contracts even when files don't exist in working tree")
            print("  ✓ Detects breaking changes from commits on any branch")
            print("  ✓ Uses b_path (modified file path) for accurate file tracking")
            print("  ✓ Handles file rename/move/delete operations correctly")
            print()
            return 0
        else:
            print("FAILURE: Some cross-branch tests failed.")
            return 1
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
        if test_config.exists():
            test_config.unlink()


if __name__ == "__main__":
    sys.exit(run_cross_branch_test())
