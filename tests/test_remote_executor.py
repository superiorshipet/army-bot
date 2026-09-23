from armybot.domain.entities import DeploymentPlan
from armybot.domain.enums import ProjectStack
from armybot.infrastructure.deploy.remote_executor import RemoteRecipeDeployExecutor


def test_node_site_script_contains_nextjs_support():
    plan = DeploymentPlan(
        project_name="my-next-app",
        repo_url="https://github.com/example/my-next-app",
        branch="main",
        stack=ProjectStack.Node,
        build_steps=["npm install", "npm run build"],
        runtime="node",
        notes=[],
        app_path=".",
        app_entry="",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._node_site_script(plan, server, "https://example.com/my-next-app/")

    assert "IS_NEXT=false" in script
    assert 'grep -q \'"next"\' package.json' in script
    assert "proxy_pass http://127.0.0.1:$PORT;" in script
    assert "basePath" in script


def test_static_site_script_contains_vite_base():
    plan = DeploymentPlan(
        project_name="my-vite-app",
        repo_url="https://github.com/example/my-vite-app",
        branch="main",
        stack=ProjectStack.Vite,
        build_steps=["npm install", "npm run build"],
        runtime="nginx-static",
        notes=[],
        app_path=".",
        app_entry="index.html",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._static_site_script(plan, server, "https://example.com/my-vite-app/")

    assert '--base="$ROUTE_PREFIX"' in script


def test_dotnet_site_script_contains_frontend_support():
    plan = DeploymentPlan(
        project_name="my-dotnet-app",
        repo_url="https://github.com/example/my-dotnet-app",
        branch="main",
        stack=ProjectStack.DotNet,
        build_steps=["dotnet restore", "dotnet publish -c Release"],
        runtime="systemd",
        notes=[],
        app_path=".",
        app_entry="",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._dotnet_site_script(plan, server, "https://example.com/my-dotnet-app/")

    assert "CLIENT_DIR=" in script
    assert "Building frontend client in $CLIENT_DIR" in script
    assert "location ^~ /my-dotnet-app/api/" in script
    assert "location ^~ /my-dotnet-app/scalar/" in script
    assert "try_files \\$uri \\$uri/ $ROUTE_PREFIX/index.html;" in script


def test_python_site_script_contains_frontend_support():
    plan = DeploymentPlan(
        project_name="my-fastapi-app",
        repo_url="https://github.com/example/my-fastapi-app",
        branch="main",
        stack=ProjectStack.Python,
        build_steps=["pip install -r requirements.txt"],
        runtime="systemd",
        notes=[],
        app_path=".",
        app_entry="",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._python_site_script(plan, server, "https://example.com/my-fastapi-app/")

    assert "CLIENT_DIR=" in script
    assert "Building frontend client in $CLIENT_DIR" in script
    assert "location ^~ /my-fastapi-app/api/" in script
    assert "location ^~ /my-fastapi-app/docs" in script
    assert "try_files \\$uri \\$uri/ $ROUTE_PREFIX/index.html;" in script


def test_common_git_prep_contains_db_and_env():
    prep = RemoteRecipeDeployExecutor._common_git_prep("test-proj", "https://github.com/ex/test", "main", "/var/www", ".")
    assert "DATABASE_URL=" in prep
    assert "GROQ_API_KEY" in prep
    assert "CREATE DATABASE" in prep
    assert "systemctl is-active mongod" in prep


def test_node_site_script_contains_asset_normalization_and_prisma():
    plan = DeploymentPlan(
        project_name="my-node-app",
        repo_url="https://github.com/example/my-node-app",
        branch="main",
        stack=ProjectStack.Node,
        build_steps=["npm install"],
        runtime="node",
        notes=[],
        app_path=".",
        app_entry="",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._node_site_script(plan, server, "https://example.com/my-node-app/")
    assert "prisma db push" in script
    assert "route_prefix = sys.argv[2]" in script
    assert "images/" in script


def test_static_site_script_contains_asset_normalization():
    plan = DeploymentPlan(
        project_name="my-static-app",
        repo_url="https://github.com/example/my-static-app",
        branch="main",
        stack=ProjectStack.Static,
        build_steps=[],
        runtime="nginx-static",
        notes=[],
        app_path=".",
        app_entry="index.html",
    )
    server = {
        "base_path": "/var/www",
        "public_base_url": "https://example.com",
    }
    script = RemoteRecipeDeployExecutor._static_site_script(plan, server, "https://example.com/my-static-app/")
    assert "route_prefix = sys.argv[2]" in script
    assert "images/" in script


def test_all_scripts_pass_bash_syntax_check():
    import subprocess

    server = {"base_path": "/var/www", "public_base_url": "https://example.com"}
    plan = DeploymentPlan(
        project_name="my-app",
        repo_url="https://github.com/ex/my-app",
        branch="main",
        stack=ProjectStack.Node,
        build_steps=[],
        runtime="node",
        notes=[],
        app_path=".",
        app_entry="index.html",
    )
    for func in [
        RemoteRecipeDeployExecutor._static_site_script,
        RemoteRecipeDeployExecutor._node_site_script,
        RemoteRecipeDeployExecutor._python_site_script,
        RemoteRecipeDeployExecutor._dotnet_site_script,
        RemoteRecipeDeployExecutor._laravel_site_script,
        RemoteRecipeDeployExecutor._universal_script,
    ]:
        script = func(plan, server, "https://example.com/my-app/")
        p = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert p.returncode == 0, f"Bash syntax error in {func.__name__}:\n{p.stderr}"




