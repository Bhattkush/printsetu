import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Printer, QrCode, RefreshCw, Upload, Copy, Check, X, Monitor, ArrowUpRight, FileText, Settings, LayoutDashboard, Download, Plus } from 'lucide-react';
import './styles.css';
import { Account } from './account';
import { Landing } from './landing';
import { ShopSettings, PaymentRecords, Onboarding } from './shop-settings';
const API = '/api';
const AGENT_SERVER = `${location.protocol}//${location.hostname}:8000`;
const money = n => `INR ${Number(n || 0).toFixed(2)}`;
let csrf = sessionStorage.getItem('ps_csrf') || '';
async function request(path: string, options: RequestInit = {}) {
  options = {...options, credentials:'same-origin', headers:{...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...(options.headers || {}), ...(csrf ? {'X-CSRF-Token':csrf} : {})}};
  let response;
  try {
    response = await fetch(API + path, options);
  } catch {
    throw Error('Cannot reach the shop server. Check your Wi-Fi connection and try again.');
  }
  const data = await response.json().catch(() => {
    throw Error('The shop backend is unavailable. Please try again shortly.');
  });
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Request failed');
  if(data.csrf) { csrf = data.csrf; sessionStorage.setItem('ps_csrf',csrf); }
  return data;
}
function Badge({ children, tone = '' }) { return <span className={`badge ${tone}`}>{children}</span>; }
function IconButton({ title, children, onClick }) { return <button className="icon-button" title={title} aria-label={title} onClick={onClick}>{children}</button>; }
function CopyButton({ value }) {
  const [done, setDone] = useState(false);
  function copy() {
    const input = document.createElement('textarea'); input.value = value; document.body.appendChild(input); input.select();
    const ok = document.execCommand('copy'); input.remove(); setDone(ok);
    setTimeout(() => setDone(false), 2000);
  }
  return <IconButton title={done ? 'Copied' : 'Copy'} onClick={copy}>{done ? <Check size={16}/> : <Copy size={16}/>}</IconButton>;
}
function Shell({ title, subtitle, customer = false, actions = null, children }) {
  return <div className={customer ? 'customer-app' : 'app'}><nav className="topbar"><a className="brand" href={customer ? location.pathname : '/owner'}><span className="brand-mark"><Printer size={21}/></span>PrintSetu<span className="brand-divider"/><span className="brand-context">{customer ? 'Print station' : 'Workspace'}</span></a><div className="top-actions"><ModeBadge/>{!customer && <a className="admin-link" href={location.pathname === '/admin' ? '/owner' : '/admin'}><Settings size={17}/><span>{location.pathname === '/admin' ? 'Owner' : 'Admin'}</span></a>}{!customer && <button className="quiet" onClick={() => request('/auth/logout',{method:'POST'}).then(()=>location.href='/login').catch(e=>window.alert(e.message))}>Sign out</button>}</div></nav><main><header className="page-header"><div><h1>{title}</h1><p>{subtitle}</p></div>{actions}</header>{children}</main><footer><span>PrintSetu</span><span>Self-service printing</span></footer></div>;
}
function Owner() {
  const dialog = useRef(null);
  const [summary, setSummary] = useState<any>(null), [shopId, setShopId] = useState('');
  const [qr, setQr] = useState<any>(null), [activation, setActivation] = useState<any>(null);
  const [tab, setTab] = useState('overview'), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!activation) return;
    const previous = document.activeElement as HTMLElement;
    dialog.current?.querySelector('button')?.focus();
    function keydown(event) {
      if (event.key === 'Escape') setActivation(null);
      if (event.key !== 'Tab') return;
      const buttons = dialog.current?.querySelectorAll('button, a[href]');
      if (!buttons?.length) return;
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener('keydown', keydown);
    return () => { document.removeEventListener('keydown', keydown); previous?.focus(); };
  }, [activation]);
  async function load() { try { setSummary(await request('/owner/summary')); setError(''); } catch(e) { setError(e.message); } }
  useEffect(() => { load(); const stream = new EventSource('/api/owner/events'); stream.onmessage = event => {try {setSummary(JSON.parse(event.data));} catch {}}; const timer = setInterval(load, 15000); return () => {stream.close();clearInterval(timer);}; }, []);
  const shop = summary?.shops.find(s => s.id === shopId) || summary?.shops[0];
  useEffect(() => {
    setQr(null); setActivation(null); if (!shop) return;
    let active = true;
    request(`/owner/shops/${shop.id}/qr`).then(data => { if(active) setQr(data); }).catch(e => { if(active) setError(e.message); });
    return () => { active = false; };
  }, [shop?.id]);
  async function connect() {
    setBusy(true);
    try { setActivation(await request(`/owner/shops/${shop.id}/activation-codes`, { method: 'POST' })); }
    catch(e) { setError(e.message); } finally { setBusy(false); }
  }
  const orders = summary?.orders.filter(o => o.shop_id === shop?.id) || [];
  const devices = summary?.devices.filter(d => d.shop_id === shop?.id) || [];
  const online = d => d.status === 'ACTIVE' && d.last_seen_at && Date.now() - new Date(d.last_seen_at + 'Z').getTime() < 30000;
  const onlineCount = devices.filter(online).length;
  return <Shell title={shop?.name || 'Shop workspace'} subtitle={shop?.location || 'Your printing operations'} actions={shop && <button disabled={busy} onClick={connect}><Plus size={17}/>Connect computer</button>}>
    {error && <div className="error" role="alert">{error}</div>}
    {!summary || !shop ? <div className="empty">{!summary ? 'Loading workspace...' : 'No shops available'}</div> : <>
      <div className="workspace-nav"><div className="tabs">{([['overview', LayoutDashboard, 'Overview'], ['orders', FileText, 'Orders'], ['devices', Monitor, 'Devices'], ['pricing', Settings, 'Settings'], ['payments', FileText, 'Payments'], ['setup', Check, 'Setup']] as Array<[string, typeof LayoutDashboard, string]>).map(([key, Icon, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}><Icon size={16}/>{label}</button>)}</div>{summary.shops.length > 1 && <select aria-label="Shop" value={shop.id} onChange={e => setShopId(e.target.value)}>{summary.shops.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}</div>
      <div className="stats"><div><span>Recent orders</span><strong>{orders.length}</strong><small>Latest 100 across workspace</small></div><div><span>In queue</span><strong>{orders.filter(o => o.status === 'QUEUED').length}</strong><small>Waiting for the connector</small></div><div><span>Processing</span><strong>{orders.filter(o => o.status === 'PRINTING').length}</strong><small>Printing or spooler submitted</small></div><div><span>Computers online</span><strong>{onlineCount}<i> / {devices.length}</i></strong><small><span className={`dot ${onlineCount ? 'green' : ''}`}/>{onlineCount ? 'Receiving jobs' : 'No listener detected'}</small></div></div>
      {tab === 'overview' && <div className="overview-grid"><section><div className="section-heading"><h2><QrCode size={18}/>Shop QR</h2><Badge>Customer link</Badge></div><div className="qr-layout"><div className="qr-frame">{qr ? <img src={`data:image/png;base64,${qr.png_base64}`} alt="Shop customer QR code"/> : <QrCode size={60}/>}</div><div className="qr-details"><h3>{shop.name}</h3><p>{shop.location || 'Self-service printing'}</p>{qr && <><div className="link-row"><a href={qr.url} target="_blank" rel="noreferrer">{qr.url}</a><CopyButton value={qr.url}/></div><div className="button-row"><a className="button secondary" href={`data:image/png;base64,${qr.png_base64}`} download={shop.slug + '-qr.png'}><Download size={16}/>Download QR</a><a className="button quiet" href={qr.url} target="_blank" rel="noreferrer">Open shop<ArrowUpRight size={16}/></a></div><Badge tone={['127.0.0.1','localhost'].includes(new URL(qr.url).hostname) ? 'amber' : 'green'}>{['127.0.0.1','localhost'].includes(new URL(qr.url).hostname) ? 'PC-only address' : 'Local network address'}</Badge></>}</div></div></section><section className="connection-section"><div className="section-heading"><h2><Monitor size={18}/>Shop connection</h2><span className={`dot ${onlineCount ? 'green' : ''}`}/></div><div className="device-art"><Monitor size={40}/><Printer size={27}/></div><h3>{onlineCount ? 'Ready for new jobs' : 'Connect your shop computer'}</h3><p>{devices.length ? devices.length + ' computers connected' : 'No computer connected yet'}</p><button className="secondary" disabled={busy} onClick={connect}><Plus size={16}/>Connect computer</button></section></div>}
      {['overview','orders'].includes(tab) && <section className="orders-section"><div className="section-heading"><h2>Recent orders</h2><IconButton title="Refresh orders" onClick={load}><RefreshCw size={17}/></IconButton></div>{orders.length ? <div className="table-scroll"><table><thead><tr><th>Order</th><th>Print setup</th><th>Status</th><th>Payment</th><th className="align-right">Estimate</th></tr></thead><tbody>{orders.map(o => <tr key={o.id}><td className="order-number">{o.order_number}</td><td>{o.options.copies} copies / {o.options.paper_size} / {o.options.color_mode === 'bw' ? 'B&W' : 'Color'}</td><td><Badge>{o.print_job?.adapter_mode === 'mock' ? 'Mock / ' : ''}{(o.print_job?.status || o.status).replaceAll('_',' ').toLowerCase()}</Badge></td><td><Badge tone="amber">{o.demo_label}</Badge></td><td className="align-right">{money(o.total_amount)}</td></tr>)}</tbody></table></div> : <div className="empty"><FileText size={30}/><h3>No orders yet</h3><p>New customer orders will appear here.</p></div>}</section>}
      {tab === 'devices' && <section className="orders-section"><h2>Connected computers</h2>{devices.length ? devices.map(d => <div key={d.id} className="device-row"><Monitor size={24}/><div><strong>{d.name}</strong><p>{d.selected_printer || 'Printer not selected'}</p></div><Badge tone={online(d) ? 'green' : ''}>{d.status === 'REVOKED' ? 'Revoked' : online(d) ? 'Online' : 'Offline'}</Badge><button className="secondary" disabled={d.status === 'REVOKED'} onClick={async () => { if(!window.confirm('Revoke this computer? It will stop receiving jobs.')) return; try {await request('/owner/devices/'+d.id,{method:'DELETE'}); load();} catch(e:any){setError(e.message);} }}>Revoke</button></div>) : <div className="empty"><Monitor size={30}/><h3>No computers connected</h3></div>}</section>}
      {tab === 'pricing' && <ShopSettings key={shop.id} shop={shop} request={request} onSave={load}/>}
      {tab === 'payments' && <PaymentRecords request={request}/>}
      {tab === 'setup' && <Onboarding devices={devices} onConnect={connect}/>}
      <div className="financial-row"><span>Live captured revenue: <strong>{money(orders.filter(o=>o.payment_mode === 'live' && o.payment_status === 'PAID').reduce((sum,o)=>sum+o.total_amount,0))}</strong></span><span>Finished mock jobs: {orders.filter(o=>o.print_job?.adapter_mode === 'mock' && o.status === 'COMPLETED').length}</span></div>
    </>}
    {activation && <div className="modal-backdrop" onClick={() => setActivation(null)}><section ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby="connect-title" onClick={e => e.stopPropagation()}><div className="section-heading"><span className="brand-mark"><Monitor size={23}/></span><IconButton title="Close" onClick={() => setActivation(null)}><X size={19}/></IconButton></div><h2 id="connect-title">Connect {shop.name}</h2><p>Shop Connector</p><label className="field-label">One-time connection code</label><div className="code-row"><strong>{activation.code}</strong><CopyButton value={activation.code}/></div><p className="expiry">Expires {new Date(activation.expires_at + 'Z').toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</p><label className="field-label">Server address</label><div className="link-row"><span>{activation.server_url || AGENT_SERVER}</span><CopyButton value={activation.server_url || AGENT_SERVER}/></div><a className="button secondary wide" href={API + '/agent/download'}><Download size={16}/>Download Windows app</a><div className="modal-footer"><ModeBadge/><button className="secondary" onClick={() => setActivation(null)}>Done<Check size={16}/></button></div></section></div>}
  </Shell>;
}
function Customer({ slug }) {
  const [shop, setShop] = useState<any>(null), [doc, setDoc] = useState<any>(null), [order, setOrder] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [opts, setOpts] = useState({ color_mode: 'bw', paper_size: 'A4', duplex: false, page_range: 'all', copies: 1 });
  useEffect(() => { request('/shops/' + slug).then(setShop).catch(e => setError(e.message)); }, [slug]);
  useEffect(() => { if(!order) return; const stream = new EventSource('/api/orders/'+order.id+'/events'); stream.onmessage = event => {try {const data=JSON.parse(event.data);setOrder(data);if(['COMPLETED','FAILED'].includes(data.status))stream.close();}catch{}};const timer=setInterval(()=>request('/orders/'+order.id).then(setOrder).catch(e=>setError(e.message)),15000);return()=>{stream.close();clearInterval(timer);}; }, [order?.id]);
  async function upload(selected) {
    setFile(selected); setDoc(null); setPreviewPage(1); setError(''); if(!selected) return; setBusy(true);
    try { const form = new FormData(); form.append('file', selected); setDoc(await request(`/shops/${shop.id}/documents`, {method:'POST',body:form})); }
    catch(e) { setError(e.message); } finally { setBusy(false); }
  }
  async function submit() {
    setBusy(true); setError('');
    try { setOrder(await request(`/shops/${shop.id}/orders`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...opts,document_id:doc.id})})); }
    catch(e) { setError(e.message); } finally { setBusy(false); }
  }
  const [calculated, setCalculated] = useState<any>(null), [quoting, setQuoting] = useState(false), [previewPage, setPreviewPage] = useState(1);
  useEffect(() => {
    setCalculated(null); if(!doc || !shop) return; let active = true; setQuoting(true);
    const timer = setTimeout(() => request('/shops/'+shop.id+'/quote',{method:'POST',body:JSON.stringify({...opts,document_id:doc.id})}).then(data=>{if(active) {setCalculated(data);setError('');}}).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setQuoting(false);}),250);
    return () => {active=false;clearTimeout(timer);};
  },[doc?.id,opts.color_mode,opts.paper_size,opts.duplex,opts.copies,opts.page_range]);
  async function pay(existing = order) {
    setBusy(true);setError('');
    try {
      const activeOrder = existing || await request('/shops/'+shop.id+'/orders',{method:'POST',body:JSON.stringify({...opts,document_id:doc.id})});
      setOrder(activeOrder);
      const checkout = await request('/payments/create-order',{method:'POST',body:JSON.stringify({order_id:activeOrder.id})});
      if(!(window as any).Razorpay) await new Promise<void>((resolve,reject)=>{const script=document.createElement('script');script.src='https://checkout.razorpay.com/v1/checkout.js';script.onload=()=>resolve();script.onerror=()=>reject(Error('Checkout could not load. Check your connection.'));document.head.appendChild(script);});
      const popup = new (window as any).Razorpay({key:checkout.key_id,amount:checkout.amount_paise,currency:checkout.currency,order_id:checkout.razorpay_order_id,name:shop.name,description:checkout.mode === 'test' ? 'SANDBOX - test printing payment' : 'Document printing',handler:async(result:any)=>{try {const verified=await request('/payments/verify',{method:'POST',body:JSON.stringify({...result,order_id:activeOrder.id})});if(!verified.confirmed)setError('Payment is awaiting capture. Check payment status before retrying checkout.');setOrder(await request('/orders/'+activeOrder.id));}catch(e:any){setError(e.message);}},modal:{ondismiss:()=>setBusy(false)}});
      popup.on('payment.failed', (result:any)=>setError(result.error?.description || 'Payment failed. You can retry checkout.'));
      popup.open();
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  async function checkPayment() {
    setBusy(true); setError('');
    try {
      const result = await request('/payments/reconcile', {method:'POST', body:JSON.stringify({order_id:order.id})});
      setOrder(await request('/orders/'+order.id));
      if (!result.confirmed) setError('Payment has not been captured yet. No print job has been released.');
    } catch(e:any) { setError(e.message); } finally { setBusy(false); }
  }
  const option = (key, value) => setOpts(current => ({...current, [key]:value}));
  const price = (calculated?.amount_paise || 0) / 100;
  return <Shell customer title={shop?.name || 'Scan & Print'} subtitle={shop?.location || 'Your document. Your print settings.'}>{error && <div className="error">{error}</div>}{!shop ? <div className="empty">{error ? 'Shop unavailable' : 'Loading shop...'}</div> : <>
    {!shop.policy.checkout_ready && shop.policy.payment_mode !== 'disabled' && <div className="error">Payment checkout is unavailable until gateway credentials are configured.</div>}
    <div className="step-bar"><span className="done">01 Document</span><span className={doc ? 'done' : ''}>02 Settings</span><span className={order ? 'done' : ''}>03 Job status</span></div>
    <div className="customer-grid"><section><h2>Document</h2><label className="upload-zone" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy && !order)upload(e.dataTransfer.files[0]);}}><input aria-label="Choose PDF" type="file" accept="application/pdf" disabled={busy || !!order} onChange={e => upload(e.target.files[0])}/>{doc ? <FileText size={34}/> : <Upload size={34}/>}<strong>{busy && !doc ? 'Uploading...' : file?.name || 'Choose a PDF'}</strong><span>{doc ? doc.page_count + ' pages / Ready to print' : 'PDF document'}</span>{doc && <Badge tone="green"><Check size={13}/>Uploaded</Badge>}</label>{doc && <button className="quiet" disabled={!!order} onClick={() => {setDoc(null);setFile(null);}}>Remove document<X size={15}/></button>}{doc && <div className="preview-tool"><img src={'/api/customer/documents/'+doc.id+'/preview?page='+previewPage} alt={'Document page '+previewPage}/><div className="preview-controls"><button className="secondary" disabled={previewPage === 1} onClick={()=>setPreviewPage(previewPage-1)}>Previous</button><span>{previewPage} / {doc.page_count}</span><button className="secondary" disabled={previewPage === doc.page_count} onClick={()=>setPreviewPage(previewPage+1)}>Next</button></div></div>}</section>
    <section><h2>Print settings</h2><fieldset disabled={!!order || busy}><label className="field-label">Color</label><div className="segmented">{[['bw','Black & white'],['color','Color']].map(([key,label]) => <button key={key} className={opts.color_mode === key ? 'selected' : ''} disabled={key === 'color' && !!shop.printers?.length && !shop.printers.some(p=>p.supports_color)} onClick={() => option('color_mode',key)}>{label}</button>)}</div><div className="form-grid"><label>Paper size<select value={opts.paper_size} onChange={e => option('paper_size',e.target.value)}>{['A4','A3'].map(size=><option key={size} disabled={!!shop.printers?.length && !shop.printers.some(p=>p.paper_sizes.includes(size))}>{size}</option>)}</select></label><label>Copies<input type="number" min="1" max="100" value={opts.copies} onChange={e => option('copies',Number(e.target.value))}/></label></div><label>Page range<input value={opts.page_range} onChange={e => option('page_range',e.target.value)} placeholder="all or 1-3,5"/></label><label className="check"><input type="checkbox" checked={opts.duplex} disabled={!!shop.printers?.length && !shop.printers.some(p=>p.supports_duplex)} onChange={e => option('duplex',e.target.checked)}/>Double-sided</label></fieldset><div className="estimate"><div><span>Estimated total</span><strong>{money(price)}</strong></div><Badge tone="amber">{shop.policy.payment_mode === 'disabled' ? 'UNPAID' : shop.policy.payment_mode === 'test' ? 'RAZORPAY TEST' : 'LIVE'}</Badge></div>{calculated && <p className="price-breakdown">{calculated.pages} pages x {calculated.copies} copies x {money(calculated.rate_paise/100)} / Base {money(calculated.base_paise/100)}</p>}<button className="wide" disabled={!doc || busy || quoting || !calculated || !!order || !Number.isInteger(opts.copies) || opts.copies < 1 || opts.copies > 100 || (shop.policy.payment_mode !== 'disabled' && !shop.policy.checkout_ready)} onClick={() => shop.policy.payment_mode === 'disabled' ? submit() : pay(null)}><Printer size={18}/>{order ? 'Job submitted' : busy ? 'Submitting...' : shop.policy.payment_mode === 'disabled' ? 'Submit demo print job' : shop.policy.payment_mode === 'test' ? 'Pay & print (sandbox)' : 'Pay & print'}</button></section></div>
    {order && <section className="receipt"><div className="section-heading"><h2><FileText size={18}/>{order.order_number}</h2><Badge tone="amber">{order.demo_label} / {order.payment_status}</Badge></div>{order.payment_mode !== 'demo' && order.payment_status !== 'PAID' && <><button className="secondary" disabled={busy} onClick={checkPayment}><RefreshCw size={16}/>Check payment status</button><button disabled={busy || !shop.policy.checkout_ready} onClick={()=>pay(order)}>Retry checkout</button></>}<a className="button secondary" href={'/api/orders/'+order.id+'/receipt'}><Download size={16}/>Download receipt</a><div className="receipt-status"><strong>{(order.print_job?.status || order.status).replaceAll('_',' ').toLowerCase()}</strong><span>{order.print_job?.adapter_mode === 'mock' ? 'Mock mode - no physical printing' : order.print_job?.status === 'SUBMITTED_TO_SPOOLER' ? 'Submitted to Windows spooler; physical completion unconfirmed' : 'Order received'}</span></div>{order.print_job?.error && <div className="error">{order.print_job.error}</div>}</section>}
  </>}</Shell>;
}
function Admin() {
  const [data,setData] = useState<any>(null), [message,setMessage] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  useEffect(() => { request('/admin/overview').then(setData).catch(e => setError(e.message)); }, []);
  async function cleanup() { setBusy(true); try { setMessage(JSON.stringify(await request('/admin/cleanup',{method:'POST'}))); } catch(e) {setError(e.message);} finally {setBusy(false);} }
  return <Shell title="Operations" subtitle="Workspace policy and file retention">{error && <div className="error">{error}</div>}{data && <><div className="stats">{[['Shops',data.shops],['Orders',data.orders],['Print jobs',data.jobs]].map(([label,count]) => <div key={label}><span>{label}</span><strong>{count}</strong></div>)}</div><section className="orders-section"><h2>Printing policy</h2><div className="policy-row"><span>Payment required</span><Badge>{data.policy.payment_required ? 'Enabled' : 'Disabled'}</Badge></div><div className="policy-row"><span>Local unpaid printing</span><Badge tone="amber">{data.policy.unpaid_printing_allowed ? 'Allowed in demo' : 'Blocked'}</Badge></div></section><section className="orders-section"><div className="section-heading"><h2>Temporary files</h2><button className="secondary" disabled={busy} onClick={cleanup}><RefreshCw size={16}/>{busy ? 'Cleaning...' : 'Clean expired files'}</button></div>{message && <output>{message}</output>}</section><AdminMonitoring data={data}/></>}</Shell>;
}
function AdminMonitoring({ data }) {
  return <>
    <section className="orders-section"><h2>Accounts & devices</h2><div className="policy-row"><span>Registered users</span><strong>{data.users}</strong></div><div className="policy-row"><span>Registered devices</span><strong>{data.devices}</strong></div><div className="policy-row"><span>Payment environment</span><Badge>{data.policy.payment_mode}</Badge></div></section>
    <section className="orders-section"><h2>Failed jobs</h2>{data.failed_jobs?.length ? data.failed_jobs.map(job=><div className="policy-row" key={job.id}><code>{job.id}</code><span>{job.error || 'No error detail'}</span></div>) : <p>No failed jobs.</p>}</section>
    <section className="orders-section"><h2>Cleanup failures</h2>{data.cleanup_failures?.length ? data.cleanup_failures.map(doc=><div className="policy-row" key={doc.id}><code>{doc.id}</code><span>{doc.error}</span></div>) : <p>No cleanup failures.</p>}</section>
    <section className="orders-section"><h2>Audit log</h2><div className="table-scroll"><table><thead><tr><th>Time</th><th>Action</th><th>Record</th></tr></thead><tbody>{data.audit_logs?.map((entry,index)=><tr key={index}><td>{new Date(entry.created_at+'Z').toLocaleString()}</td><td>{entry.action}</td><td>{entry.entity_id}</td></tr>)}</tbody></table></div></section>
  </>;
}
function ModeBadge() {
  const [policy,setPolicy]=useState<any>(null);
  useEffect(()=>{request('/health').then(setPolicy).catch(()=>{});},[]);
  return <Badge tone="amber">{policy?.payment_mode === 'test' ? 'RAZORPAY SANDBOX' : policy?.payment_mode === 'live' ? 'LIVE' : 'DEMO MODE'}</Badge>;
}
function Protected({ children }: {children: React.ReactNode}) {
  const [session,setSession]=useState<any>(null),[loading,setLoading]=useState(true);
  useEffect(()=>{request('/auth/me').then(setSession).catch(()=>{}).finally(()=>setLoading(false));},[]);
  if(loading)return <div className="skeleton"/>;
  return session ? <>{children}</> : <Account request={request}/>;
}
function App() {
  const path=location.pathname;
  if(path.startsWith('/shop/'))return <Customer slug={path.split('/').pop()}/>;
  if(path === '/register')return <Account request={request} register/>;
  if(path === '/login')return <Account request={request}/>;
  if(path.startsWith('/admin'))return <Protected><Admin/></Protected>;
  if(path.startsWith('/owner'))return <Protected><Owner/></Protected>;
  return <Landing/>;
}
createRoot(document.getElementById('root')).render(<App/>);
