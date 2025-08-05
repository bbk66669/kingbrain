# /.collab/README-collab.md

## 上线前须知
1. **首次运行**
   ```bash
   make validate && make lock-hash   # 生成 SHA256 并完成全量校验
   （脚本会自动替换 spec.lock.yaml 内的 REPLACE_ME）
