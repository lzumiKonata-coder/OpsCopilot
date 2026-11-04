import React, { useEffect, useState } from 'react';
import { Plus, Server, Search, RefreshCw, Loader2 } from 'lucide-react';
import type { Host } from '../types';
import { hostService } from '../services/hostService';
import { HostCard } from '../components/host/HostCard';
import { RegisterHostModal } from '../components/host/RegisterHostModal';

export const DashboardView: React.FC = () => {
  const [hosts, setHosts] = useState<Host[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);

  const fetchHosts = async () => {
    setLoading(true);
    try {
      const data = await hostService.getHosts();
      setHosts(data);
    } catch (err) {
      console.error('Failed to fetch hosts:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHosts();
  }, []);

  const filteredHosts = hosts.filter(
    (h) =>
      h.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      h.ip.includes(searchTerm) ||
      h.username.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '28px 24px', width: '100%' }}>
      {/* 资产列表头部工具栏 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '24px',
          flexWrap: 'wrap',
          gap: '16px',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <h1 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-main)' }}>
              主机资产管理
            </h1>
            <span
              style={{
                fontSize: '12px',
                padding: '2px 10px',
                borderRadius: '12px',
                backgroundColor: 'rgba(56, 139, 253, 0.15)',
                color: '#58a6ff',
                fontWeight: 600,
                border: '1px solid rgba(56, 139, 253, 0.3)',
              }}
            >
              {hosts.length} 台在线资产
            </span>
          </div>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>
            点击对应主机卡片【打开终端】即可秒开 WebSSH 并联动 OpsCopilot 智能助手！
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* 搜索框 */}
          <div
            style={{
              position: 'relative',
              width: '240px',
            }}
          >
            <Search
              size={14}
              color="#8b949e"
              style={{ position: 'absolute', left: '10px', top: '10px' }}
            />
            <input
              className="form-control"
              placeholder="搜索主机别名、IP 或用户名..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{ paddingLeft: '32px' }}
            />
          </div>

          <button
            className="btn"
            onClick={fetchHosts}
            title="刷新主机列表"
            disabled={loading}
          >
            {loading ? <Loader2 size={14} className="spin-animate" /> : <RefreshCw size={14} />}
            <span>刷新</span>
          </button>

          <button
            className="btn btn-primary"
            onClick={() => setIsModalOpen(true)}
          >
            <Plus size={15} />
            <span>登记新主机</span>
          </button>
        </div>
      </div>

      {/* 主机资产卡片网格 */}
      {loading ? (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '80px 0',
            color: 'var(--text-muted)',
            gap: '12px',
          }}
        >
          <Loader2 size={32} className="spin-animate" color="#58a6ff" />
          <span>正在加载主机资产列表...</span>
        </div>
      ) : filteredHosts.length > 0 ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
            gap: '20px',
          }}
        >
          {filteredHosts.map((host) => (
            <HostCard key={host.host_id || host.ip} host={host} onRefresh={fetchHosts} />
          ))}
        </div>
      ) : (
        <div
          style={{
            textAlign: 'center',
            padding: '80px 20px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px dashed var(--border)',
            borderRadius: '12px',
          }}
        >
          <Server size={44} color="#8b949e" style={{ marginBottom: '16px' }} />
          <h3 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '6px' }}>
            {searchTerm ? '未检索到匹配的主机资产' : '当前尚未登记任何目标主机'}
          </h3>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '20px' }}>
            {searchTerm
              ? '请尝试更换搜索关键字，或清除检索内容'
              : '登记一台 SSH 机器，立即开启智能终端与排障'}
          </p>
          <button
            className="btn btn-primary"
            onClick={() => {
              if (searchTerm) setSearchTerm('');
              else setIsModalOpen(true);
            }}
          >
            {searchTerm ? '清除筛选条件' : '立即登记第一台主机'}
          </button>
        </div>
      )}

      {/* 登记主机模态框 */}
      <RegisterHostModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSuccess={fetchHosts}
      />
    </div>
  );
};
