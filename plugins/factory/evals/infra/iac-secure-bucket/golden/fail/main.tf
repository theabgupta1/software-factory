provider "aws" {
  access_key = "EXAMPLE-ACCESS-KEY-ID"
  secret_key = "abc"
}

resource "aws_s3_bucket" "logs" {
  bucket = "app-logs"
  acl    = "public-read"
}

data "aws_iam_policy_document" "all" {
  statement {
    actions   = ["*"]
    resources = ["*"]
  }
}
