// scripts/puppeteer.config.cjs
module.exports = {
  launchOptions: {
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox'
    ]
  }
}
