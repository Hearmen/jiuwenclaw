# Linux 跨节点 NFS 使用说明

这两个脚本用于在 Linux 节点之间共享 `jiuwenclaw` 工作空间。

适用场景：

- 一个中心节点作为 NFS server
- 一个或多个节点作为 NFS client
- 多个节点共享同一个工作空间目录

默认共享目录：

```text
/root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

建议：

- 优先使用内网 IP
- 所有节点都先完成一次 `jiuwenclaw` 初始化
- 尽量使用同一个用户运行，这里默认是 `root`

## 1. 服务端执行

在中心节点执行：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh --client-ip <客户端内网IP>
```



如果要自定义路径：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <客户端内网IP> \
  --export-dir /root/.jiuwenclaw/agent/jiuwenclaw_workspace \
  --mount-point /root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

## 2. 客户端执行

在每个客户端节点执行：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh --server-ip <服务端内网IP>
```



如果要自定义路径：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh \
  --server-ip <服务端内网IP> \
  --export-dir /root/.jiuwenclaw/agent/jiuwenclaw_workspace \
  --mount-point /root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

## 3. 连通性检查

在客户端执行：

```bash
rpcinfo -p <服务端内网IP>
showmount -e <服务端内网IP>
```

如果两条命令都能正常返回，就说明 NFS 服务已经可达。

## 4. 挂载后检查

在客户端执行：

```bash
mount | grep jiuwenclaw_workspace
df -h | grep jiuwenclaw_workspace
```

## 5. 同步验证

在服务端执行：

```bash
echo hello > /root/.jiuwenclaw/agent/jiuwenclaw_workspace/hello.txt
```

在客户端执行：

```bash
cat /root/.jiuwenclaw/agent/jiuwenclaw_workspace/hello.txt
```

再在客户端追加：

```bash
echo world >> /root/.jiuwenclaw/agent/jiuwenclaw_workspace/hello.txt
```

回到服务端查看：

```bash
cat /root/.jiuwenclaw/agent/jiuwenclaw_workspace/hello.txt
```

如果两边都能看到相同内容，就说明同步成功。

## 6. 说明

- 客户端脚本会在挂载前备份已有本地目录
- 如果有多个客户端，每个客户端都执行一次客户端脚本即可
- 这套方案共享的是文件系统，不是多节点分布式运行时
