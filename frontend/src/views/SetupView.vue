<template>
  <main class="auth-page"><span class="wordmark">CURSOR PANEL<span class="brand-dot">.</span></span><h1>首次使用</h1>
    <p>本实例由管理员部署。管理员在服务器终端完成首次初始化，再使用创建的邮箱与密码登录。</p>
    <ol class="steps"><li>管理员按部署文档配置数据目录与独立密钥，运行 <code>cursor-core server-init</code>。</li><li>登录后直接使用个人空间；需要共享时创建团队空间并复制邀请链接。</li><li>团队成员默认看不到账号，管理员逐个分配查看或使用权限。</li></ol>
    <p>忘记密码请联系实例管理员进行离线恢复。恢复后，旧登录会话会全部撤销。</p>
    <RouterLink class="button primary" to="/login">返回登录</RouterLink>
  </main>
</template>
