migrate((app) => {
  const service = '@request.auth.collectionName = "service_accounts" && @request.auth.role = "daemon" && @request.auth.revoked = false';
  app.save(new Collection({name: 'service_accounts', type: 'auth',
    listRule: 'id = @request.auth.id', viewRule: 'id = @request.auth.id',
    createRule: null, updateRule: null, deleteRule: null,
    authRule: 'revoked = false', passwordAuth: {enabled: true, identityFields: ['email']},
    fields: [{name: 'role', type: 'select', values: ['daemon'], required: true, maxSelect: 1},
             {name: 'revoked', type: 'bool'}]
  }));
  const projections = ['effective_config', 'mandates', 'markets', 'sources', 'opportunities', 'decisions',
    'orders', 'fills', 'positions', 'strategies', 'strategy_stats', 'model_calls', 'wallet_signals',
    'daily_pnl', 'cashflows', 'treasury_recommendations', 'alerts', 'health', 'incidents'];
  projections.forEach((name) => app.save(new Collection({name: name, type: 'base',
    listRule: service, viewRule: service, createRule: service, updateRule: service, deleteRule: null,
    fields: [
      {name: 'business_id', type: 'text', required: true, max: 200},
      {name: 'schema_version', type: 'number', required: true, min: 1, onlyInt: true},
      {name: 'revision', type: 'number', required: true, min: 1, onlyInt: true},
      {name: 'payload', type: 'json', required: true, maxSize: 1048576},
      {name: 'status', type: 'text', max: 100},
      {name: 'as_of', type: 'date', required: true}
    ], indexes: ['CREATE UNIQUE INDEX idx_' + name + '_business ON ' + name + ' (business_id)']
  })));
  ['approval_requests', 'config_requests', 'commands'].forEach((name) => app.save(new Collection({name: name, type: 'base',
    listRule: service, viewRule: service, createRule: service, updateRule: service, deleteRule: null,
    fields: [
      {name: 'business_id', type: 'text', required: true, max: 200},
      {name: 'kind', type: 'text', required: true, max: 100},
      {name: 'summary', type: 'text', max: 3000},
      {name: 'payload', type: 'json', required: true, maxSize: 1048576},
      {name: 'payload_hash', type: 'text', required: true, pattern: '^[a-f0-9]{64}$'},
      {name: 'nonce', type: 'text', required: true, max: 200},
      {name: 'revision', type: 'number', required: true, min: 1, onlyInt: true},
      {name: 'amount', type: 'text', pattern: '^(0|[1-9][0-9]*)([.][0-9]+)?$'},
      {name: 'currency', type: 'text', max: 40},
      {name: 'expires_at', type: 'date', required: true},
      {name: 'human_decision', type: 'select', required: true, maxSelect: 1, values: ['PENDING', 'APPROVED', 'REJECTED']},
      {name: 'execution_status', type: 'select', required: true, maxSelect: 1, values: ['WAITING', 'VALIDATING', 'ACCEPTED', 'EXECUTING', 'APPLIED', 'EXPIRED', 'INVALIDATED', 'FAILED', 'UNKNOWN']},
      {name: 'execution_result', type: 'json', maxSize: 1048576},
      {name: 'decided_by', type: 'text', max: 200},
      {name: 'decided_at', type: 'date'},
      {name: 'simulated', type: 'bool'}
    ], indexes: ['CREATE UNIQUE INDEX idx_' + name + '_business ON ' + name + ' (business_id)',
      'CREATE UNIQUE INDEX idx_' + name + '_nonce ON ' + name + ' (nonce)']
  })));
  app.save(new Collection({name: 'approval_audit', type: 'base', listRule: service, viewRule: service,
    createRule: null, updateRule: null, deleteRule: null,
    fields: [{name: 'business_id', type: 'text', required: true},
      {name: 'request_collection', type: 'text', required: true}, {name: 'request_id', type: 'text', required: true},
      {name: 'actor_id', type: 'text', required: true}, {name: 'before', type: 'json', required: true},
      {name: 'after', type: 'json', required: true}, {name: 'decided_at', type: 'date', required: true},
      {name: 'simulated', type: 'bool'}],
    indexes: ['CREATE UNIQUE INDEX idx_approval_audit_business ON approval_audit (business_id)']
  }));
  const settings = app.settings();
  settings.meta.appName = 'EdgeHunter - CONTROL SIMULADO LOCAL';
  settings.logs.logIP = false;
  settings.logs.logAuthId = true;
  app.save(settings);
}, () => { throw new Error('Financial control migrations cannot be rolled back destructively. Restore isolated and reconcile.'); });
