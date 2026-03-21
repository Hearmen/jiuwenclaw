# 单元测试指南

## 📋 目录

- [安装测试依赖](#安装测试依赖)
- [本地运行测试](#本地运行测试)
- [测试覆盖率](#测试覆盖率)
- [编写新测试](#编写新测试)
- [CI/CD 配置](#cicd-配置)

---

## 🔧 安装测试依赖

### 安装测试依赖

```bash
# 方式 1: 使用 pip
pip install -e ".[test]"

# 方式 2: 直接安装测试包
pip install pytest pytest-asyncio pytest-cov pytest-mock coverage freezegun
```

---

## 🏃 本地运行测试

### 运行所有测试

```bash
# 运行所有测试
pytest

# 或者指定目录
pytest tests/
```

### 运行特定测试文件

```bash
# 运行单个测试文件
pytest tests/unit/test_config.py

# 运行特定目录的测试
pytest tests/unit/
```

### 运行特定测试用例

```bash
# 运行特定测试函数
pytest tests/unit/test_config.py::TestResolveEnvVars::test_resolve_string_with_env_var

# 运行特定测试类
pytest tests/unit/test_config.py::TestResolveEnvVars
```

### 常用测试选项

```bash
# 详细输出
pytest -v

# 显示打印输出
pytest -s

# 显示错误堆栈
pytest --tb=long

# 只运行失败的测试
pytest --lf

# 遇到第一个失败就停止
pytest -x

# 并行运行测试（需要安装 pytest-xdist）
pytest -n auto
```

### 运行带标记的测试

```bash
# 只运行单元测试
pytest -m unit

# 只运行集成测试
pytest -m integration

# 只运行慢速测试
pytest -m slow

# 排除慢速测试
pytest -m "not slow"
```

---

## 📊 测试覆盖率

### 生成覆盖率报告

```bash
# 生成终端报告
pytest --cov=jiuwenclaw --cov-report=term-missing

# 生成 HTML 报告
pytest --cov=jiuwenclaw --cov-report=html

# 生成 XML 报告（用于 CI）
pytest --cov=jiuwenclaw --cov-report=xml
```

### 查看覆盖率报告

```bash
# 生成 HTML 报告后在浏览器中打开
pytest --cov=jiuwenclaw --cov-report=html
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### 覆盖率目标

- **总体目标**: 80% 以上
- **关键模块**: 90% 以上
- **新增代码**: 100%

---

## ✏️ 编写新测试

### 测试文件结构

```
tests/
├── __init__.py
├── conftest.py              # 共享 fixtures
├── unit/                    # 单元测试
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_evolution_schema.py
│   └── ...
└── integration/             # 集成测试
    ├── __init__.py
    └── ...
```

### 测试命名规范

```python
# 文件名: test_<module_name>.py
# 例如: test_config.py, test_signal_detector.py

# 测试类: Test<ClassName>
# 例如: TestResolveEnvVars, TestSignalDetector

# 测试函数: test_<specific_behavior>
# 例如: test_resolve_string_with_env_var, test_detect_execution_failure
```

### 测试模板

```python
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for <module_name>."""

import pytest

from jiuwenclaw.<module> import <ClassOrFunction>


class Test<ClassName>:
    """Test <ClassName>."""

    def test_<specific_behavior>(self):
        """Test that <expected_behavior>."""
        # Arrange
        input_data = "test_input"

        # Act
        result = <function_under_test>(input_data)

        # Assert
        assert result == "expected_output"

    def test_<edge_case>(self):
        """Test <edge_case_description>."""
        # Test implementation
        pass

    @pytest.mark.parametrize("input,expected", [
        ("test1", "result1"),
        ("test2", "result2"),
    ])
    def test_<parameterized>(self, input, expected):
        """Test with different inputs."""
        result = <function_under_test>(input)
        assert result == expected
```

### 使用 Fixtures

```python
import pytest

def test_with_fixture(temp_workspace: Path):
    """Test using temp_workspace fixture."""
    assert temp_workspace.exists()
    assert (temp_workspace / "config").exists()

def test_with_custom_fixture(sample_messages):
    """Test using custom sample_messages fixture."""
    assert len(sample_messages) > 0
```

### 异步测试

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await async_function()
    assert result is not None
```

---

## 🔄 CI/CD 配置

### GitHub Actions 工作流

项目配置了以下 GitHub Actions 工作流：

#### 1. **Tests** (`.github/workflows/test.yml`)

自动运行在：
- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- 手动触发

测试矩阵：
- Python 版本: 3.11, 3.12, 3.13
- 操作系统: Ubuntu Latest

#### 2. **Code Quality** (`.github/workflows/lint.yml`)

包括：
- 类型检查 (mypy)
- 代码格式检查 (black)
- Linting (ruff)
- 安全扫描 (bandit)

### CI 中的环境变量

CI 会自动设置测试所需的环境变量：

```yaml
env:
  MODEL_PROVIDER: "test_provider"
  MODEL_NAME: "test_model"
  API_BASE: "https://test.api.com"
  API_KEY: "test_key"
```

---

## 📝 开发工作流

### 在开发新功能时

1. **编写测试**: 先编写测试用例
   ```bash
   # 创建测试文件
   touch tests/unit/test_new_feature.py
   ```

2. **实现功能**: 编写代码使测试通过
   ```bash
   # 运行测试
   pytest tests/unit/test_new_feature.py -v
   ```

3. **检查覆盖率**: 确保新代码有足够覆盖
   ```bash
   pytest --cov=jiuwenclaw.new_feature --cov-report=term-missing
   ```

4. **提交代码**: 确保所有测试通过
   ```bash
   git add .
   git commit -m "feat: add new feature with tests"
   git push
   ```

### 修复 Bug 时

1. **编写失败测试**: 编写一个能重现 Bug 的测试
2. **修复 Bug**: 修改代码使测试通过
3. **验证**: 运行所有测试确保没有引入新问题

---

## 🛠️ 故障排除

### 常见问题

#### 1. 导入错误

```bash
# 确保项目已安装
pip install -e .

# 或者设置 PYTHONPATH
export PYTHONPATH=/Users/gawa/Desktop/pr/jiuwenclaw:$PYTHONPATH
```

#### 2. Fixture 未找到

```bash
# 确保 conftest.py 在正确的位置
ls tests/conftest.py

# 查看可用 fixtures
pytest --fixtures
```

#### 3. 覆盖率报告为空

```bash
# 确保使用正确的源码路径
pytest --cov=jiuwenclaw --cov-report=term-missing tests/

# 检查是否在正确的目录
cd /Users/gawa/Desktop/pr/jiuwenclaw
pytest
```

---

## 📚 更多资源

- [Pytest 文档](https://docs.pytest.org/)
- [pytest-cov 文档](https://pytest-cov.readthedocs.io/)
- [pytest-asyncio 文档](https://pytest-asyncio.readthedocs.io/)
- [Effective Python Testing with Pytest](https://pragprog.com/titles/bopytest/)

---

## 🎯 测试最佳实践

1. **测试独立性**: 每个测试应该独立运行，不依赖其他测试
2. **清晰命名**: 测试名称应清楚描述测试的内容
3. **AAA 模式**: Arrange-Act-Assert 模式组织测试代码
4. **使用 Fixture**: 复用测试设置代码
5. **Mock 外部依赖**: 隔离被测试的代码
6. **测试边界情况**: 不仅仅是正常情况
7. **保持简单**: 一个测试只验证一个行为
8. **快速反馈**: 单元测试应该快速运行

---

**需要帮助？** 请查看项目 README 或联系维护者。
