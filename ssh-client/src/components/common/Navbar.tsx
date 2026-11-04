import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Terminal, Server } from 'lucide-react';

export const Navbar: React.FC = () => {
  const location = useLocation();

  return (
    <header className="top-navbar">
      <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
        <Link to="/" className="brand">
          <Terminal size={20} color="#58a6ff" />
          <span>OpsCopilot</span>
        </Link>

        <nav style={{ display: 'flex', gap: '8px' }}>
          <Link
            to="/"
            className={`btn btn-sm ${location.pathname === '/' ? 'btn-primary' : ''}`}
            style={{ textDecoration: 'none' }}
          >
            <Server size={14} />
            主机资产
          </Link>
        </nav>
      </div>
    </header>
  );
};
