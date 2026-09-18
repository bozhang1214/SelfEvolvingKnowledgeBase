package com.sekb.ondevice

import android.app.Application

/**
 * 应用入口：只放"进程级单例"的装配（没有引入 DI 框架——这个体量用不上）。
 */
class SekbApp : Application()
