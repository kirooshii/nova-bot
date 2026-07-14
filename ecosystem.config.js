module.exports = {
  apps: [
    {
      name: "nova-bot",
      script: "bot.py",
      interpreter: ".venv/bin/python",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        NODE_ENV: "production",
      },
    },
    {
      name: "nova-app",
      script: "app.py",
      interpreter: ".venv/bin/python",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
