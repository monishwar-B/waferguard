output "cluster_name" { value = module.eks.cluster_name }
output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "efs_id" { value = aws_efs_file_system.images.id }
output "artifacts_bucket" { value = aws_s3_bucket.artifacts.bucket }
output "redis_url" { value = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0" }
output "database_url" {
  value     = "postgresql+psycopg://waferguard:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/waferguard"
  sensitive = true
}
