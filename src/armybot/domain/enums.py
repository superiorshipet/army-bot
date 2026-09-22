from enum import StrEnum


class UserRole(StrEnum):
    SuperAdmin = "super_admin"
    User = "user"


class UserStatus(StrEnum):
    Pending = "pending"
    Active = "active"
    Rejected = "rejected"
    Suspended = "suspended"


class CredentialProvider(StrEnum):
    GitHub = "github"
    AWS = "aws"
    Cloudflare = "cloudflare"
    Server = "server"


class DeploymentStatus(StrEnum):
    Queued = "queued"
    Planning = "planning"
    Running = "running"
    Successful = "successful"
    Failed = "failed"
    RolledBack = "rolled_back"


class ProjectStack(StrEnum):
    Unknown = "unknown"
    Vite = "vite"
    React = "react"
    DotNet = "dotnet"
    Laravel = "laravel"
    Node = "node"
    Python = "python"
    Static = "static"
