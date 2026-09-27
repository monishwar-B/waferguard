variable "region" {
  type    = string
  default = "ap-south-1"
}
variable "env" {
  type    = string
  default = "prod"
}
variable "cluster_version" {
  type    = string
  default = "1.30"
}
variable "node_instance_types" {
  type    = list(string)
  default = ["c6i.xlarge"]   # add a g5.xlarge GPU node group for TensorRT inference if needed
}
variable "node_min" {
  type    = number
  default = 2
}
variable "node_max" {
  type    = number
  default = 8
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}
variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}
