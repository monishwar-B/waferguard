data "aws_availability_zones" "available" { state = "available" }

locals {
  name = "waferguard-${var.env}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
}

# ---------------------------------------------------------------- network
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name                 = local.name
  cidr                 = "10.40.0.0/16"
  azs                  = local.azs
  private_subnets      = ["10.40.1.0/24", "10.40.2.0/24", "10.40.3.0/24"]
  public_subnets       = ["10.40.101.0/24", "10.40.102.0/24", "10.40.103.0/24"]
  database_subnets     = ["10.40.201.0/24", "10.40.202.0/24", "10.40.203.0/24"]
  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  public_subnet_tags   = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags  = { "kubernetes.io/role/internal-elb" = 1 }
}

# ---------------------------------------------------------------- kubernetes
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.13"

  cluster_name                             = local.name
  cluster_version                          = var.cluster_version
  vpc_id                                   = module.vpc.vpc_id
  subnet_ids                               = module.vpc.private_subnets
  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = {}
    aws-efs-csi-driver     = {}
  }

  eks_managed_node_groups = {
    general = {
      instance_types = var.node_instance_types
      min_size       = var.node_min
      max_size       = var.node_max
      desired_size   = var.node_min
    }
  }
}

# ---------------------------------------------------------------- container registry
resource "aws_ecr_repository" "app" {
  name                 = "waferguard"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# ---------------------------------------------------------------- PostgreSQL
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_security_group" "data" {
  name   = "${local.name}-data"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "postgres" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16.3"
  instance_class               = var.db_instance_class
  allocated_storage            = 50
  max_allocated_storage        = 500
  storage_encrypted            = true
  db_name                      = "waferguard"
  username                     = "waferguard"
  password                     = random_password.db.result
  db_subnet_group_name         = module.vpc.database_subnet_group_name
  vpc_security_group_ids       = [aws_security_group.data.id]
  multi_az                     = var.env == "prod"
  backup_retention_period      = 14
  deletion_protection          = var.env == "prod"
  skip_final_snapshot          = var.env != "prod"
  final_snapshot_identifier    = "${local.name}-final"
  performance_insights_enabled = true
}

# ---------------------------------------------------------------- Redis
resource "aws_elasticache_subnet_group" "redis" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = local.name
  description                = "WaferGuard job queue and event bus"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.data.id]
  at_rest_encryption_enabled = true
}

# ---------------------------------------------------------------- shared image storage (EFS, RWX)
resource "aws_efs_file_system" "images" {
  creation_token   = local.name
  encrypted        = true
  performance_mode = "generalPurpose"
  lifecycle_policy { transition_to_ia = "AFTER_30_DAYS" }
}

resource "aws_security_group" "efs" {
  name   = "${local.name}-efs"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
}

resource "aws_efs_mount_target" "images" {
  count           = length(module.vpc.private_subnets)
  file_system_id  = aws_efs_file_system.images.id
  subnet_id       = module.vpc.private_subnets[count.index]
  security_groups = [aws_security_group.efs.id]
}

# ---------------------------------------------------------------- model artifacts / exports bucket
resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name}-artifacts-"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
