# Winodws VS Code ACP Client插件配置流程
1. 安装 ACP Client 插件，标识符：`formulahendry.acp-client`
2. 插件内点击 `ACP: Add Agent Configuration`
3. `Name`：`jiuwenclaw`
4. `Command`：`D:\ACP\jiuwenclaw-ACP\jiuwenclaw\scripts\run_gateway_acp.cmd`
5. `Config`：留空
6. 先在命令行启动主进程：`python -m jiuwenclaw.app`
7. 插件内连接 `jiuwenclaw` 后在 chat 窗口进行对话

# MacOS/Linux VS Code ACP Client插件配置流程
1. 安装 ACP Client 插件，标识符：`formulahendry.acp-client`
2. 插件内点击 `ACP: Add Agent Configuration`
3. `Add Acp Agent`：`jiuwenclaw`
4. `Agent Command`：`./scripts/run_gateway_acp.sh(run_gateway_acp.sh的绝对路径)`
5. `Agent Arguments`：留空
6. 在终端执行添加可执行权限：chmod +x scripts/run_gateway_acp.sh 
7. 先在命令行启动主进程：`python -m jiuwenclaw.app`
8. 插件内连接 `jiuwenclaw` 后在 chat 窗口进行对话
