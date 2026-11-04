"""适配层 - 解释器载荷递归审计（Python -c 等高危代码）"""

import ast as py_ast
import re
from typing import List, Tuple

from domain.rules import FORBIDDEN_DESTRUCTIVE_PATHS, SENSITIVE_FILES


def _audit_python_payload(py_code: str) -> Tuple[List[str], List[str], List[str]]:
    """递归审计 python -c 载荷中潜在的高危与毁灭性代码"""
    hit_rules: List[str] = []
    forbidden: List[str] = []
    high_risk: List[str] = []

    try:
        tree = py_ast.parse(py_code)
        for node in py_ast.walk(tree):
            if isinstance(node, py_ast.Call):
                func_name = ""
                if isinstance(node.func, py_ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, py_ast.Attribute):
                    func_name = node.func.attr

                if func_name in ("rmtree",):
                    target = ""
                    if node.args and isinstance(node.args[0], py_ast.Constant):
                        target = str(node.args[0].value)
                    if target in FORBIDDEN_DESTRUCTIVE_PATHS or target.rstrip("/") in ("/", "", "*"):
                        hit_rules.append("PYTHON_FORBIDDEN_RMTREE_ROOT")
                        forbidden.append(f"Python shutil.rmtree targeting critical path '{target}' is strictly blocked.")
                    else:
                        hit_rules.append("PYTHON_HIGH_RISK_RMTREE")
                        high_risk.append(f"Python shutil.rmtree on path '{target}' is high risk.")

                elif func_name in ("remove", "unlink", "rmdir"):
                    target = ""
                    if node.args and isinstance(node.args[0], py_ast.Constant):
                        target = str(node.args[0].value)
                    if target in FORBIDDEN_DESTRUCTIVE_PATHS or target.rstrip("/") in ("/", "", "*"):
                        hit_rules.append(f"PYTHON_FORBIDDEN_{func_name.upper()}_ROOT")
                        forbidden.append(f"Python {func_name} targeting critical root path '{target}' is strictly blocked.")
                    else:
                        hit_rules.append(f"PYTHON_HIGH_RISK_{func_name.upper()}")
                        high_risk.append(f"Python filesystem deletion call '{func_name}' is high risk.")

                elif func_name in ("system", "popen", "run", "call", "check_call", "check_output", "Popen", "spawn", "spawnl", "execv", "execve"):
                    cmd_str = None
                    cmd_list = None
                    if node.args:
                        first_arg = node.args[0]
                        if isinstance(first_arg, py_ast.Constant) and isinstance(first_arg.value, str):
                            cmd_str = first_arg.value
                        elif isinstance(first_arg, (py_ast.List, py_ast.Tuple)):
                            cmd_list = [
                                elt.value for elt in first_arg.elts
                                if isinstance(elt, py_ast.Constant) and isinstance(elt.value, str)
                            ]

                    if cmd_str:
                        if re.search(r"rm\s+-(?:r[fF]|[fF]r|R)\s+[/~*]", cmd_str):
                            hit_rules.append("PYTHON_SYSTEM_FORBIDDEN_RM_ROOT")
                            forbidden.append(f"Python command execution contains catastrophic deletion: {cmd_str}")
                        else:
                            hit_rules.append(f"PYTHON_SYSTEM_CALL:{func_name}")
                            high_risk.append(f"Python execution of system command: {cmd_str[:40]}")
                    elif cmd_list:
                        if cmd_list and cmd_list[0] == "rm":
                            is_rec = any(
                                a in ("-r", "-rf", "-fr", "-R", "--recursive") or
                                (a.startswith("-") and ("r" in a or "R" in a))
                                for a in cmd_list[1:]
                            )
                            has_root = any(
                                a in FORBIDDEN_DESTRUCTIVE_PATHS or
                                a.rstrip("/") in ("/", "", "*")
                                for a in cmd_list[1:] if not a.startswith("-")
                            )
                            if is_rec and has_root:
                                hit_rules.append("PYTHON_SUBPROCESS_FORBIDDEN_RM_ROOT")
                                forbidden.append(f"Python subprocess execution strictly prohibited: {' '.join(cmd_list)}")
                            else:
                                hit_rules.append("PYTHON_SUBPROCESS_RM")
                                high_risk.append(f"Python subprocess executes rm command: {' '.join(cmd_list)}")
                        else:
                            hit_rules.append(f"PYTHON_SUBPROCESS_CALL:{func_name}")
                            high_risk.append(f"Python subprocess execution: {' '.join(cmd_list[:3])}")
                    else:
                        hit_rules.append(f"PYTHON_DYNAMIC_EXECUTION:{func_name}")
                        high_risk.append(f"Python dynamic subprocess/system execution with '{func_name}'.")

            # 敏感文件字面量检测
            if isinstance(node, py_ast.Constant) and isinstance(node.value, str):
                for sens in SENSITIVE_FILES:
                    if sens in node.value:
                        hit_rules.append(f"PYTHON_SENSITIVE_FILE:{sens}")
                        high_risk.append(f"Python payload references sensitive path '{sens}'.")

    except Exception:
        # Python 代码 AST 语法解析失败时的正则兜底扫描
        if re.search(r"(?:rmtree|remove|unlink|rm)\s*\(?\s*['\"][/~*]", py_code) or re.search(r"rm\s+-(?:r[fF]|[fF]r|R)\s+[/~*]", py_code):
            hit_rules.append("PYTHON_REGEX_FORBIDDEN_RMTREE")
            forbidden.append("Python payload contains destructive deletion operation against root.")
        elif re.search(r"\b(rmtree|remove|unlink|rmdir|system|popen|subprocess)\b", py_code):
            hit_rules.append("PYTHON_REGEX_HIGH_RISK")
            high_risk.append("Python payload contains high-risk system operations.")

    return hit_rules, forbidden, high_risk
