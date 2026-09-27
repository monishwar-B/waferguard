terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # backend "s3" { bucket = "my-tf-state" key = "waferguard/terraform.tfstate" region = "ap-south-1" }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = "waferguard", env = var.env } }
}
