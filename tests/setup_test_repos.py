#!/usr/bin/env python3
"""
创建测试用的模拟 Git 仓库
用于验证跨仓库变更追踪工具的功能
"""

import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from git import Repo


def create_test_repo(base_dir: Path, repo_name: str, repo_type: str, issue_id: str) -> Path:
    """创建一个测试用的 Git 仓库"""
    repo_path = base_dir / repo_name
    repo_path.mkdir(parents=True, exist_ok=True)
    
    os.chdir(repo_path)
    
    repo = Repo.init()
    
    config_writer = repo.config_writer()
    config_writer.set_value("user", "name", "Test User")
    config_writer.set_value("user", "email", "test@example.com")
    config_writer.release()
    
    readme_content = f"# {repo_name}\n\n这是一个测试仓库，用于演示跨仓库变更追踪。\n"
    (repo_path / "README.md").write_text(readme_content)
    
    api_dir = repo_path / "api"
    api_dir.mkdir(exist_ok=True)
    
    models_dir = repo_path / "models"
    models_dir.mkdir(exist_ok=True)
    
    if repo_type == "backend":
        (repo_path / "user.proto").write_text(_get_proto_content("User"))
        (api_dir / "openapi.yaml").write_text(_get_openapi_content())
        (models_dir / "user.py").write_text(_get_python_model_content("User"))
    else:
        (api_dir / "userApi.ts").write_text(_get_ts_api_content())
        (models_dir / "user.ts").write_text(_get_ts_model_content("User"))
    
    repo.index.add(["README.md"])
    repo.index.commit(f"Initial commit for {repo_name}")
    
    commit_times = [
        datetime.now() - timedelta(hours=3),
        datetime.now() - timedelta(hours=2),
        datetime.now() - timedelta(hours=1),
    ]
    
    commits = [
        {
            "message": f"feat: 实现用户基本信息接口 [{issue_id}]\n\n- 添加用户查询接口\n- 修改用户信息接口",
            "files": _get_commit_files(repo_type, 1),
        },
        {
            "message": f"fix: 修复用户状态更新的 BUG [{issue_id}]\n\nBREAKING CHANGE: 用户状态字段类型变更",
            "files": _get_commit_files(repo_type, 2),
        },
        {
            "message": f"feat: 添加用户等级系统 [{issue_id}]",
            "files": _get_commit_files(repo_type, 3),
        },
    ]
    
    for i, commit_info in enumerate(commits):
        for file_path, content in commit_info["files"].items():
            full_path = repo_path / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
        
        repo.index.add([str(p) for p, _ in commit_info["files"].items()])
        
        commit = repo.index.commit(commit_info["message"])
        
        commit_date = commit_times[i].timestamp()
        repo.git.commit(
            "--amend",
            "--no-edit",
            f"--date={commit_date}",
            author="Test User <test@example.com>",
        )
    
    return repo_path


def _get_proto_content(name: str) -> str:
    return f"""syntax = "proto3";

package user;

service {name}Service {{
  rpc Get{name}({name}Request) returns ({name}Response);
  rpc Update{name}({name}UpdateRequest) returns ({name}Response);
}}

message {name}Request {{
  string id = 1;
}}

message {name}Response {{
  string id = 1;
  string name = 2;
  string email = 3;
  int32 status = 4;
}}

message {name}UpdateRequest {{
  string id = 1;
  string name = 2;
  int32 status = 3;
}}
"""


def _get_openapi_content() -> str:
    return """openapi: 3.0.0
info:
  title: User Service API
  version: 1.0.0
paths:
  /api/v1/users/{id}:
    get:
      summary: 获取用户信息
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 成功
    put:
      summary: 更新用户信息
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 成功
  /api/v1/users/{id}/level:
    get:
      summary: 获取用户等级
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 成功
components:
  schemas:
    User:
      type: object
      properties:
        id:
          type: string
        name:
          type: string
        email:
          type: string
        status:
          type: integer
        level:
          type: integer
"""


def _get_python_model_content(name: str) -> str:
    return f"""from dataclasses import dataclass
from typing import Optional


@dataclass
class {name}Model:
    \"\"\"用户模型\"\"\"
    id: str
    name: str
    email: str
    status: int
    level: Optional[int] = None


@dataclass
class {name}DTO:
    \"\"\"用户数据传输对象\"\"\"
    id: str
    name: str
    status: int


class {name}Schema:
    \"\"\"用户序列化模式\"\"\"
    id: str
    name: str
    email: str
    status: int
"""


def _get_ts_api_content() -> str:
    return """import axios from 'axios';

export interface UserRequest {
  id: string;
}

export interface UserResponse {
  id: string;
  name: string;
  email: string;
  status: number;
  level: number;
}

export async function getUser(id: string): Promise<UserResponse> {
  const response = await axios.get(`/api/v1/users/${id}`);
  return response.data;
}

export async function updateUser(id: string, data: Partial<UserResponse>): Promise<UserResponse> {
  const response = await axios.put(`/api/v1/users/${id}`, data);
  return response.data;
}

export async function getUserLevel(id: string): Promise<{ level: number }> {
  const response = await axios.get(`/api/v1/users/${id}/level`);
  return response.data;
}
"""


def _get_ts_model_content(name: str) -> str:
    return f"""export interface {name}Model {{
  id: string;
  name: string;
  email: string;
  status: number;
  level?: number;
}}

export type {name}Status = 'active' | 'inactive' | 'suspended';

export interface {name}DTO {{
  id: string;
  name: string;
  status: {name}Status;
}}

export class {name}ViewModel {{
  id: string;
  name: string;
  displayName: string;
  
  constructor(data: {name}Model) {{
    this.id = data.id;
    this.name = data.name;
    this.displayName = `${{data.name}} (${{data.status}})`;
  }}
}}
"""


def _get_commit_files(repo_type: str, commit_num: int) -> dict:
    """根据提交编号返回要修改的文件"""
    if repo_type == "backend":
        if commit_num == 1:
            return {
                "user.proto": _get_proto_content("User"),
                "api/openapi.yaml": _get_openapi_content(),
                "models/user.py": _get_python_model_content("User"),
            }
        elif commit_num == 2:
            return {
                "user.proto": _get_proto_content("User").replace("int32 status", "string status"),
                "models/user.py": _get_python_model_content("User").replace("status: int", "status: str"),
            }
        else:
            return {
                "user.proto": _get_proto_content("User") + "\n\nmessage LevelInfo { int32 level = 1; }",
                "models/user.py": _get_python_model_content("User") + "\n\n@dataclass\nclass LevelInfo:\n    level: int = 1\n",
            }
    else:
        if commit_num == 1:
            return {
                "src/api/userApi.ts": _get_ts_api_content(),
                "src/models/user.ts": _get_ts_model_content("User"),
            }
        elif commit_num == 2:
            return {
                "src/models/user.ts": _get_ts_model_content("User").replace("status: number", "status: string"),
            }
        else:
            return {
                "src/api/userApi.ts": _get_ts_api_content() + "\n\nexport async function getUserLevel(id: string) {\n  return { level: 1 };\n}\n",
            }


def main():
    """主函数：创建测试仓库和配置文件"""
    base_dir = Path(tempfile.mkdtemp(prefix="dep_tracker_test_"))
    issue_id = "JIRA-1024"
    
    print(f"测试目录: {base_dir}")
    print(f"Issue ID: {issue_id}")
    print()
    
    repos = [
        ("user-service", "backend"),
        ("order-service", "backend"),
        ("web-frontend", "frontend"),
    ]
    
    for repo_name, repo_type in repos:
        print(f"创建仓库: {repo_name} ({repo_type})...")
        create_test_repo(base_dir, repo_name, repo_type, issue_id)
    
    config_content = f"""repositories:
  - name: user-service
    path: {base_dir / 'user-service'}
    type: backend
    api_contract_patterns:
      - "*.proto"
      - "api/**/*.yaml"
      - "api/**/*.yml"
    shared_model_patterns:
      - "models/**/*.py"
      - "*.py"

  - name: order-service
    path: {base_dir / 'order-service'}
    type: backend
    api_contract_patterns:
      - "*.proto"
      - "api/**/*.yaml"
    shared_model_patterns:
      - "models/**/*.py"

  - name: web-frontend
    path: {base_dir / 'web-frontend'}
    type: frontend
    api_contract_patterns:
      - "src/api/**/*.ts"
    shared_model_patterns:
      - "src/models/**/*.ts"

dependency_rules:
  - from: user-service
    to: order-service
    reason: "订单服务依赖用户服务的用户信息API"
  
  - from: user-service
    to: web-frontend
    reason: "前端依赖用户服务的登录和用户信息接口"

  - from: order-service
    to: web-frontend
    reason: "前端依赖订单服务的订单查询和创建接口"
"""
    
    test_config_path = base_dir.parent / "test_config.yaml"
    test_config_path.write_text(config_content, encoding='utf-8')
    
    print()
    print(f"测试配置已生成: {test_config_path}")
    print()
    print("现在可以运行以下命令进行测试:")
    print(f"  python main.py --config {test_config_path} track {issue_id}")
    print()
    print("或者使用其他子命令:")
    print(f"  python main.py --config {test_config_path} list-repos")
    print(f"  python main.py --config {test_config_path} list-rules")
    print(f"  python main.py --config {test_config_path} check")
    print()
    print("测试完成后，可以安全删除测试目录:")
    print(f"  rd /s /q {base_dir}")
    print(f"  del {test_config_path}")
    
    return test_config_path, issue_id, base_dir


if __name__ == "__main__":
    main()
