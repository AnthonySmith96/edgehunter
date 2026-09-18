migrate((app) => {
  const seed = JSON.parse($os.getenv('EDGEHUNTER_PB_BOOTSTRAP'));
  if (!seed.admin_email || !seed.admin_password || !seed.daemon_password) throw new Error('Missing dedicated bootstrap credentials');
  const human = new Record(app.findCollectionByNameOrId('_superusers'));
  human.set('email', seed.admin_email); human.set('password', seed.admin_password); app.save(human);
  const daemon = new Record(app.findCollectionByNameOrId('service_accounts'));
  daemon.set('email', seed.daemon_email); daemon.set('password', seed.daemon_password);
  daemon.set('role', 'daemon'); daemon.set('revoked', false); app.save(daemon);
}, () => { throw new Error('Do not delete control identities by rollback'); });
