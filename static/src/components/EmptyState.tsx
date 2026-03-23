import { motion } from 'framer-motion';
import { Bot, Sparkles, Terminal, Globe, Database, Zap, ArrowRight } from 'lucide-react';

interface Props {
  hasSession: boolean;
}

const features = [
  {
    icon: Terminal,
    label: 'Bash команды',
    desc: 'Выполнение на ПК',
    gradient: 'from-emerald-500/20 to-green-600/20',
    border: 'border-emerald-500/20',
    iconColor: 'text-emerald-400',
    hoverGlow: 'hover:shadow-emerald-500/10',
  },
  {
    icon: Globe,
    label: 'Веб-поиск',
    desc: 'DuckDuckGo',
    gradient: 'from-blue-500/20 to-cyan-500/20',
    border: 'border-blue-500/20',
    iconColor: 'text-blue-400',
    hoverGlow: 'hover:shadow-blue-500/10',
  },
  {
    icon: Database,
    label: 'Память',
    desc: 'Долгосрочное хранение',
    gradient: 'from-purple-500/20 to-violet-500/20',
    border: 'border-purple-500/20',
    iconColor: 'text-purple-400',
    hoverGlow: 'hover:shadow-purple-500/10',
  },
  {
    icon: Zap,
    label: 'SSH',
    desc: 'Удалённые серверы',
    gradient: 'from-amber-500/20 to-orange-500/20',
    border: 'border-amber-500/20',
    iconColor: 'text-amber-400',
    hoverGlow: 'hover:shadow-amber-500/10',
  },
];

const container = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.1, delayChildren: 0.3 },
  },
};

const item = {
  hidden: { opacity: 0, y: 24, scale: 0.92 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] as const },
  },
};

// Orbiting particle component
function OrbitingDot({ delay, size, radius, duration, color }: {
  delay: number;
  size: number;
  radius: number;
  duration: number;
  color: string;
}) {
  return (
    <motion.div
      className="absolute"
      style={{
        width: size,
        height: size,
        left: '50%',
        top: '50%',
        marginLeft: -size / 2,
        marginTop: -size / 2,
      }}
      animate={{
        x: [
          Math.cos(0) * radius,
          Math.cos(Math.PI / 2) * radius,
          Math.cos(Math.PI) * radius,
          Math.cos(3 * Math.PI / 2) * radius,
          Math.cos(2 * Math.PI) * radius,
        ],
        y: [
          Math.sin(0) * radius,
          Math.sin(Math.PI / 2) * radius,
          Math.sin(Math.PI) * radius,
          Math.sin(3 * Math.PI / 2) * radius,
          Math.sin(2 * Math.PI) * radius,
        ],
        opacity: [0.2, 0.7, 0.2, 0.7, 0.2],
      }}
      transition={{
        duration,
        delay,
        repeat: Infinity,
        ease: 'linear',
      }}
    >
      <div className={`w-full h-full rounded-full ${color} blur-[1px]`} />
    </motion.div>
  );
}

export default function EmptyState({ hasSession }: Props) {
  if (!hasSession) {
    return (
      <div className="flex-1 flex items-center justify-center p-8 relative">
        {/* Background aurora effect */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          <motion.div
            className="absolute top-1/4 left-1/4 w-96 h-96 rounded-full"
            style={{
              background: 'radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%)',
            }}
            animate={{
              x: [0, 50, -30, 20, 0],
              y: [0, -40, 20, -10, 0],
              scale: [1, 1.2, 0.9, 1.1, 1],
            }}
            transition={{ duration: 20, repeat: Infinity, ease: 'easeInOut' }}
          />
          <motion.div
            className="absolute bottom-1/4 right-1/4 w-96 h-96 rounded-full"
            style={{
              background: 'radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 70%)',
            }}
            animate={{
              x: [0, -40, 30, -20, 0],
              y: [0, 30, -20, 40, 0],
              scale: [1, 0.9, 1.15, 0.95, 1],
            }}
            transition={{ duration: 25, repeat: Infinity, ease: 'easeInOut' }}
          />
        </div>

        <motion.div
          variants={container}
          initial="hidden"
          animate="show"
          className="text-center max-w-lg relative z-[1]"
        >
          {/* Animated logo with orbiting particles */}
          <motion.div variants={item} className="relative mx-auto mb-10 w-32 h-32">
            {/* Glow ring */}
            <motion.div
              className="absolute inset-0 rounded-3xl"
              style={{
                background: 'linear-gradient(135deg, rgba(59,130,246,0.15), rgba(168,85,247,0.15), rgba(6,182,212,0.15))',
              }}
              animate={{ rotate: [0, 360] }}
              transition={{ duration: 30, repeat: Infinity, ease: 'linear' }}
            />
            
            {/* Outer glow */}
            <div className="absolute -inset-4 rounded-[2rem] bg-gradient-to-br from-accent/10 via-purple/10 to-cyan/10 blur-2xl" />
            
            {/* Main icon container */}
            <motion.div
              className="relative w-full h-full rounded-3xl bg-gradient-to-br from-bg-secondary to-bg-tertiary border border-border/50 flex items-center justify-center overflow-hidden"
              whileHover={{ scale: 1.05 }}
              transition={{ type: 'spring', stiffness: 300, damping: 20 }}
            >
              {/* Inner gradient overlay */}
              <div className="absolute inset-0 bg-gradient-to-br from-accent/5 via-transparent to-purple/5" />
              <motion.div
                animate={{ 
                  rotate: [0, 5, -5, 3, 0],
                  scale: [1, 1.05, 0.95, 1.02, 1],
                }}
                transition={{ duration: 6, repeat: Infinity, ease: 'easeInOut' }}
              >
                <Bot size={48} className="text-accent relative z-10" strokeWidth={1.5} />
              </motion.div>
            </motion.div>

            {/* Orbiting dots */}
            <OrbitingDot delay={0} size={6} radius={75} duration={8} color="bg-accent" />
            <OrbitingDot delay={2} size={4} radius={80} duration={10} color="bg-purple" />
            <OrbitingDot delay={4} size={5} radius={70} duration={12} color="bg-cyan" />
            <OrbitingDot delay={1} size={3} radius={85} duration={9} color="bg-success" />
          </motion.div>

          <motion.h2
            variants={item}
            className="text-3xl font-extrabold text-text-primary mb-4 tracking-tight"
          >
            Добро пожаловать в{' '}
            <span className="gradient-text-animated">Thule UI</span>
          </motion.h2>

          <motion.p
            variants={item}
            className="text-sm text-text-secondary mb-12 leading-relaxed max-w-md mx-auto"
          >
            AI-ассистент с инструментами для работы с системой, интернетом и долгосрочной памятью
          </motion.p>

          <motion.div
            variants={container}
            className="grid grid-cols-2 gap-3 text-left"
          >
            {features.map((f) => (
              <motion.div
                key={f.label}
                variants={item}
                whileHover={{ scale: 1.04, y: -3 }}
                whileTap={{ scale: 0.98 }}
                className={`group flex items-center gap-3 p-4 rounded-2xl bg-gradient-to-br ${f.gradient} border ${f.border} backdrop-blur-sm cursor-default transition-all duration-300 hover:shadow-xl ${f.hoverGlow} hover-lift`}
              >
                <div className={`w-10 h-10 rounded-xl bg-bg-primary/60 flex items-center justify-center ${f.border} transition-transform duration-300 group-hover:scale-110`}>
                  <f.icon size={18} className={`${f.iconColor} transition-transform duration-300 group-hover:rotate-12`} />
                </div>
                <div>
                  <div className="text-xs font-bold text-text-primary">
                    {f.label}
                  </div>
                  <div className="text-[10px] text-text-muted mt-0.5">{f.desc}</div>
                </div>
              </motion.div>
            ))}
          </motion.div>

          {/* Hint */}
          <motion.div
            variants={item}
            className="mt-10 flex items-center justify-center gap-2 text-text-muted"
          >
            <motion.div
              animate={{ x: [0, 4, 0] }}
              transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
            >
              <ArrowRight size={14} />
            </motion.div>
            <span className="text-xs">Создайте новый чат, чтобы начать</span>
          </motion.div>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="text-center"
      >
        <motion.div
          animate={{ y: [0, -10, 0] }}
          transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
          className="relative mx-auto mb-8"
        >
          <div className="absolute inset-0 w-24 h-24 mx-auto rounded-2xl bg-accent/10 blur-2xl" />
          <motion.div
            className="relative w-24 h-24 mx-auto rounded-2xl bg-gradient-to-br from-bg-secondary to-bg-tertiary border border-border/50 flex items-center justify-center overflow-hidden"
            whileHover={{ rotate: [0, -5, 5, 0], scale: 1.05 }}
            transition={{ duration: 0.6 }}
          >
            <div className="absolute inset-0 bg-gradient-to-br from-accent/5 to-purple/5" />
            <Sparkles size={32} className="text-accent/60 relative z-10" />
          </motion.div>
        </motion.div>
        <h3 className="text-xl font-bold text-text-primary mb-2">
          Начните диалог
        </h3>
        <p className="text-sm text-text-muted max-w-xs mx-auto">
          Напишите сообщение, чтобы начать работу с вашим AI-ассистентом
        </p>
      </motion.div>
    </div>
  );
}
