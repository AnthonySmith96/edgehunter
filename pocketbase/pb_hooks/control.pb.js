// Request hooks apply even to superusers. Root/server-side code remains an explicit trust boundary.
routerAdd('GET', '/api/edgehunter/control-identity', (e) => {
  if (e.auth.getString('role') !== 'daemon' || e.auth.getBool('revoked'))
    throw new ForbiddenError('Not an active daemon');
  const email = $os.getenv('EDGEHUNTER_PB_HUMAN_EMAIL');
  if (!email) throw new ForbiddenError('Control actor is not configured');
  const human = e.app.findFirstRecordByData('_superusers', 'email', email);
  return e.json(200, {actor_id: human.id});
}, $apis.requireAuth('service_accounts'));

onRecordCreateRequest((e) => {
  const body = e.requestInfo().body;
  const daemon = e.auth && e.auth.collection().name === 'service_accounts' && e.auth.getString('role') === 'daemon' && !e.auth.getBool('revoked');
  if (!daemon) throw new ForbiddenError('Only the daemon may propose a control request');
  if (e.record.getString('human_decision') !== 'PENDING' || e.record.getString('execution_status') !== 'WAITING')
    throw new BadRequestError('New proposals must be PENDING/WAITING');
  if (body.decided_by || body.decided_at) throw new ForbiddenError('Actor is stamped by server only');
  if (body.execution_result) throw new ForbiddenError('New requests cannot have an execution result');
  if (Date.parse(e.record.getString('expires_at')) <= Date.now()) throw new BadRequestError('Proposal expired');
  e.next();
}, 'approval_requests', 'config_requests', 'commands');

onRecordUpdateRequest((e) => {
  const body = e.requestInfo().body;
  const collection = e.record.collection().name;
  e.app.runInTransaction((tx) => {
    const current = tx.findRecordById(collection, e.record.id);
    const human = e.auth && e.auth.isSuperuser() && e.auth.getString('email') === $os.getenv('EDGEHUNTER_PB_HUMAN_EMAIL');
    const daemon = e.auth && e.auth.collection().name === 'service_accounts' && e.auth.getString('role') === 'daemon' && !e.auth.getBool('revoked');
    if (!human && !daemon) throw new ForbiddenError('Not the designated control actor');
    const allowed = human ? ['human_decision'] : ['execution_status', 'execution_result'];
    // Native admin submits complete record objects: unchanged immutable fields are harmless.
    Object.keys(body).forEach((key) => {
      if (allowed.indexOf(key) < 0 && JSON.stringify(body[key]) !== JSON.stringify(current.get(key)))
        throw new ForbiddenError('Immutable or server-managed field: ' + key);
    });
    if (human) {
      if (current.getString('human_decision') !== 'PENDING') throw new BadRequestError('Decision already consumed by control plane');
      if (current.getString('execution_status') !== 'WAITING') throw new BadRequestError('Request is no longer actionable');
      if (['APPROVED', 'REJECTED'].indexOf(body.human_decision) < 0) throw new BadRequestError('Invalid human transition');
      if (Date.parse(current.getString('expires_at')) <= Date.now()) throw new BadRequestError('Proposal expired');
      const before = {human_decision: current.getString('human_decision'), payload_hash: current.getString('payload_hash'), revision: current.getInt('revision')};
      current.set('human_decision', body.human_decision);
      current.set('decided_by', e.auth.id); current.set('decided_at', new Date().toISOString());
      tx.save(current);
      const audit = new Record(tx.findCollectionByNameOrId('approval_audit'));
      audit.set('business_id', collection + ':' + current.id + ':decision');
      audit.set('request_collection', collection); audit.set('request_id', current.id);
      audit.set('actor_id', e.auth.id); audit.set('before', before);
      audit.set('after', {human_decision: current.getString('human_decision'), payload_hash: current.getString('payload_hash'), revision: current.getInt('revision')});
      audit.set('decided_at', current.getString('decided_at')); audit.set('simulated', current.getBool('simulated'));
      tx.save(audit);
    } else {
      const before = current.getString('execution_status');
      const after = body.execution_status || before;
      const transitions = {
        WAITING: ['VALIDATING', 'ACCEPTED', 'EXECUTING', 'APPLIED', 'EXPIRED', 'INVALIDATED', 'FAILED'],
        VALIDATING: ['ACCEPTED', 'EXECUTING', 'APPLIED', 'EXPIRED', 'INVALIDATED', 'FAILED'],
        ACCEPTED: ['EXECUTING', 'APPLIED', 'EXPIRED', 'INVALIDATED', 'FAILED', 'UNKNOWN'],
        EXECUTING: ['APPLIED', 'FAILED', 'UNKNOWN'], UNKNOWN: ['APPLIED', 'FAILED'],
        APPLIED: [], EXPIRED: [], INVALIDATED: [], FAILED: []
      };
      if (before !== after && (!transitions[before] || transitions[before].indexOf(after) < 0))
        throw new BadRequestError('Invalid execution transition');
      if (transitions[before] && transitions[before].length === 0 && body.execution_result !== undefined && JSON.stringify(body.execution_result) !== JSON.stringify(current.get('execution_result')))
        throw new ForbiddenError('Final execution result is immutable');
      if (['ACCEPTED', 'EXECUTING', 'APPLIED', 'UNKNOWN'].indexOf(after) >= 0 && current.getString('human_decision') !== 'APPROVED')
        throw new BadRequestError('Execution needs an approved decision');
      // A result may be projected after expiry; beginning execution after expiry is prohibited.
      if (['WAITING', 'VALIDATING'].indexOf(before) >= 0 && ['ACCEPTED', 'EXECUTING', 'APPLIED'].indexOf(after) >= 0 && Date.parse(current.getString('expires_at')) <= Date.now())
        throw new BadRequestError('Approval expired before execution');
      allowed.forEach((key) => { if (body[key] !== undefined) current.set(key, body[key]); });
      tx.save(current);
    }
    e.record = current;
  });
  return e.json(200, e.record);
}, 'approval_requests', 'config_requests', 'commands');

onRecordDeleteRequest((e) => { throw new ForbiddenError('Control requests and audit are append-only'); },
  'approval_requests', 'config_requests', 'commands', 'approval_audit');
onRecordCreateRequest((e) => { throw new ForbiddenError('Audit is server-generated'); }, 'approval_audit');
onRecordUpdateRequest((e) => { throw new ForbiddenError('Audit is immutable'); }, 'approval_audit');

onRecordUpdateRequest((e) => {
  e.app.runInTransaction((tx) => {
    const old = tx.findRecordById(e.record.collection().name, e.record.id);
    if (e.record.getString('business_id') !== old.getString('business_id')) throw new ForbiddenError('Business identity is immutable');
    if (e.record.getInt('revision') <= old.getInt('revision')) throw new BadRequestError('Projection revision must advance');
    tx.save(e.record);
  });
  return e.json(200, e.record);
}, 'effective_config', 'mandates', 'markets', 'sources', 'opportunities', 'decisions', 'orders', 'fills',
  'positions', 'strategies', 'strategy_stats', 'model_calls', 'wallet_signals', 'daily_pnl', 'cashflows',
  'treasury_recommendations', 'alerts', 'health', 'incidents');
