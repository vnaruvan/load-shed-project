variable "kubeconfig_path" {
  type        = string
  description = "Path to kubeconfig used to connect Terraform to your cluster"
  default     = "~/.kube/config"
}

