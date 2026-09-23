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

