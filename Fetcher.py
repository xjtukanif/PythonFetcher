#!/opt/sohumc/bin/python
#coding:gb18030

import sys, os, traceback
sys.path.append(os.path.dirname(__file__))
import socket, time
from collections import deque

from twisted.internet import epollreactor
epollreactor.install()
import twisted.web.client
import twisted.internet
from twisted.internet import reactor, task
from twisted.web.client import HTTPClientFactory, getPage
from twisted.python import failure

'''
����:
    1 Fetcher��һ��ʹ��twistedʵ�ֵ�֧�ֲ���ץȡ��spider
    2 ��֧�ְ���host����ץȡ�ٶȿ��ƣ�����host��ץȡ�����ͬ
    3 Fetcher�ĳ�ʱΪȫ�ֳ�ʱ���̶�Ϊ30��
����:
    1 ��֧��dns����
    2 ��֧��host�Ĳ�ͬ�ٶȿ��Ʋ���
    3 ץȡ����host���е��ȣ�������serv 
'''

__version__ = '1.0'
__revision__ = '$Revision: 1 $'

socket.setdefaulttimeout(30)

def getHost(url):
    return url.split('/')[2]

class HTTPClientHeaderFactory(HTTPClientFactory):
    #��дHTTPClientFactory�� �Ի�ȡheaders
    def __init__(self, url, method='GET', postdata=None, headers=None,
                 agent="Twisted PageGetter", timeout=90, cookies=None,
                 followRedirect=1, redirectLimit=6):
        self.redirect_urls = []
        HTTPClientFactory.__init__(self, url, method, postdata, headers, agent, timeout, cookies, followRedirect, redirectLimit)                

    def page(self, page):
        if self.waiting:
            self.waiting = 0
            if self.redirect_urls:
                self.redirect_urls = self.redirect_urls[1:]
            self.deferred.callback((self.response_headers, page, self.redirect_urls))
    
    def setURL(self, url):
        HTTPClientFactory.setURL(self, url)
        #request����Ҳ��������
        self.redirect_urls.append(url)

class HttpFetchResult(object):
    #���ظ��ⲿ��ץȡ���
    def __init__(self, request):
        self.request = None
        self.userdata = None
        self.page = None
        self.error = None
        self.headers = None
        self.redircet_urls = []

        if isinstance(request, tuple):
            self.request = request[0]
            self.userdata = request[1]
        else:
            self.request = request
            self.userdata = None
 
    def setError(self, error):
        self.error = error

    def setPage(self, page, headers, redicect_urls):
        self.page = page
        self.headers = headers
        self.redircet_urls = redicect_urls

    def __str__(self):
        return str(self.page)

twisted.web.client.HTTPClientFactory = HTTPClientHeaderFactory

class HttpFetchClient(object):
    def getPage(self, request, callback, *args, **kwargs):
        if isinstance(request, tuple):
            url = request[0]
            if request[1] == None:
                request = url
        else:
            url = request
        deferrd = getPage(url, *args, **kwargs)
        deferrd.addCallback(self.handleFetchReuslt, request, callback)
        deferrd.addErrback(self.handleFetchReuslt, request, callback)

    def handleFetchReuslt(self, result, request, callback, *args, **kwargs):
        fetchresult = HttpFetchResult(request)
        if isinstance(result, failure.Failure):
            fetchresult.setError(result)
        else:
            headers, page, rediect_urls = result
            fetchresult.setPage(page, headers, rediect_urls)
        callback(fetchresult, *args, **kwargs)

'''
�ö������һ��host�µ�����ץȡ���󣬲��Ը�host��������
'''
class HostRequest(object):
    IDLE, READY, WAIT, FETCH = 0, 1, 2, 3
    def __init__(self, fetchperiod=0):
        self.fetchperiod = fetchperiod
        self.maxfetch = 1
        self.fetchstatus = self.READY
        self.nextfetchtime = 0
        self.lastfetchtime = 0
        self.requests = deque()
        self.client = HttpFetchClient()
        self.nfetch = 0

    def addRequest(self, request):
        self.requests.append(request)

    def getRequest(self):
        return self.requests.popleft()
    
    def toIdle(self):
        self.fetchstatus = self.IDLE

    def toWait(self):
        self.fetchstatus = self.WAIT

    def toFetch(self):
        self.fetchstatus = self.FETCH

    def toReady(self):
        self.ready = self.READY

    def isIdle(self):
        return self.fetchstatus == self.IDLE

    def isWait(self):
        return self.fetchstatus == self.WAIT

    def isFetch(self):
        return self.fetchstatus == self.FETCH

    def isReady(self):
        return self.fetchstatus == self.READY
    
    def empty(self):
        return len(self.requests) == 0

class HttpFetch(object):
    #ץȡ���Ĳ���
    DEFAULT_UA = "Sogou web spider/4.0(+http://www.sogou.com/docs/help/webmasters.htm#07)"
    def __init__(self, fetchcallback, fetchperiod=1, maxconnection=500, useragent=None):
        #FIXME
        waitmax = 600
        self.fetchcallback = fetchcallback
        self.fetchperiod = fetchperiod
        self.maxconnection = maxconnection
        self.hostcachetime = 3600
        self.curconnection = 0
        self.httprequest = {}
        self.recv = 0
        self.finish = 0
        self.waithost = []
        self.readyhost = deque() 
        self.idlehost = set()
        self.loopcalls = []
        self.registerLoopCallback(1, self.checkHostStatus, True)
        self.client = HttpFetchClient()
        self.lastcheck = int(time.time())
        for x in range(waitmax):
            self.waithost.append(deque())
        self.waitout = self.lastcheck % len(self.waithost)
        if not useragent:
            self.useragent = self.DEFAULT_UA
        else:
            self.useragent = useragent
        self.host_fetch_period = {}
    
    def addFetchRequest(self, url, userdata=None):
        """
        @function: �ⲿ�ύץȡ����Ľṹ
        @param url: ��Ҫץȡ��url
        @param userdata:  ��������
        """
        self.recv += 1
        host = getHost(url)
        if host not in self.httprequest:
            fetchperiod = self.host_fetch_period.get(host, self.fetchperiod)
            self.httprequest[host] = HostRequest(fetchperiod)
            
        hostrequest = self.httprequest[host]
        hostrequest.addRequest((url, userdata))
        if hostrequest.isFetch() or hostrequest.isWait():
            return 
        elif hostrequest.isIdle():
            self.idlehost.discard(host)
            hostrequest.toReady()

        self.readyhost.append(host)         
        self.checkReady()
    
    def setHostFetchPeriod(self, host, fetchperiod):
        """
        @function: ��������host��ץȡ���
        """
        self.host_fetch_period[host] = fetchperiod

    def fetch(self, request):
        self.client.getPage(request, self.handleFetchResult, agent=self.useragent)
        self.curconnection += 1
    
    def handleFetchResult(self, result, *args, **kwargs):
        """
        @function: ץȡ��Ϻ�Ļص�����
        @param result: ץȡ���
        @param args: δʹ��
        @param kwargs: δʹ��
        """
        self.curconnection -= 1
        self.finish += 1
        request = result.request
        if isinstance(request, tuple):
            url, userdata = request
            if userdata is None:
                request = url
        else:
            url = request
        host = getHost(url)
        
        self.lastfetchtime = int(time.time())
        hostrequest = self.httprequest[host]
        hostrequest.nfetch += 1
        self.speedControl(host, hostrequest, self.lastfetchtime)

        try:
            self.fetchcallback(result)
        except Exception, e:
            print >>sys.stderr, traceback.format_exc()

        self.checkReady()
    
    def speedControl(self, host, hostrequest, now):
        """
        ���һ��host�����ٶȿ���
        """
        reach_max = False
        if hostrequest.nfetch >= hostrequest.maxfetch:
            reach_max = True
            hostrequest.nextfetchtime = now + hostrequest.fetchperiod
            if hostrequest.fetchperiod == 0:
                reach_max = False
                hostrequest.nfetch = 0
        if reach_max:
            hostrequest.toWait()
            entry = hostrequest.nextfetchtime % len(self.waithost)
            self.waithost[entry].append(host)
            hostrequest.nfetch = 0
        else:
            self.hostToReadyOrIdle(host, hostrequest)

    def hostToReadyOrIdle(self, host, hostrequest):
        if hostrequest.empty():
            hostrequest.toIdle()
            self.idlehost.add(host)
        else:
            hostrequest.toReady()
            self.readyhost.append(host)
    
    def checkHostStatus(self, now=None):
        """
        ��Ҫ������ѭ�����ɶ�ʱ�����ƶ��ڵ���
        """
        if now is None:
            now = int(time.time())
        self.checkWait(now)
        self.checkReady(now)
        self.checkIdle(now)

    def checkWait(self, now):
        while self.lastcheck < now:
            self.waitout += 1 
            if self.waitout == len(self.waithost):
                self.waitout = 0
            waithost = self.waithost[self.waitout]
            while waithost:
                host = waithost.popleft()
                hostrequest = self.httprequest[host]
                self.hostToReadyOrIdle(host, hostrequest)
            self.lastcheck +=1
    
    def checkReady(self, now=None):
        while self.readyhost:
            if self.curconnection >= self.maxconnection:
                break
            host = self.readyhost.popleft()
            self.__submitHostRequest(host)

    def checkIdle(self, now):
        pass

    def __submitHostRequest(self, host):
        hostrequest = self.httprequest[host]
        hostrequest.toFetch()
        self.fetch(hostrequest.getRequest())
    
    def run(self):
        """
        ��ʼ����twisted reactor����
        ע�⣺�˺���һ�����þͽ��뵽�¼��������ⲿ�ڴ˺���֮��Ĵ��붼�����ᱻִ�С�
             �����Ҫ�ڴ�֮��ִ�д��룬��ʹ��registerCallbackע��ص�
        """
        for looptask, period, now in self.loopcalls:
            looptask.start(period, now)
        reactor.run()

    def stop(self):
        """
        ��ֹtwsited reactor����
        ע�⣺�˺���һ�����ã����������˳�
        """
        for looptask, period, now in self.loopcalls:
            looptask.stop()
        if reactor.running:
            reactor.stop()
        
    def registerCallback(self, timelater, callback, *args, **kwargs):
        """
        @function: ���ⲿע����ʱ�ص������Ľӿڣ��Ա����ⲿ֪��reactor�Ĵ���
        """
        return reactor.callLater(timelater, callback, *args, **kwargs)

    def registerLoopCallback(self, period, callback, now, *args, **kwargs):
        """
        @function: ���ⲿע��ѭ����ʱ���Ľӿڣ��Ա����ⲿ֪��reactor�Ĵ���
        """
        loop_task = task.LoopingCall(callback, *args, **kwargs)
        self.loopcalls.append((loop_task, period, now))
        return loop_task, period

def main():
    global fetchnum, needfetch
    def handleFetchResult(result):
        global fetchnum
        page = result.page
        error = result.error
        userdata = result.userdata
        redircet_urls = result.redircet_urls
        print int(time.time()),  'error:%s user_data:%s redircet_urls:%s' % (error, userdata, redircet_urls)
        fetchnum += 1
        if fetchnum == needfetch: 
            httpfetch.stop()
    def _main(httpfetch):
        global needfetch
        for line in sys.stdin:
            needfetch += 1
            httpfetch.addFetchRequest(line.strip())
    needfetch = 0
    fetchnum = 0
    httpfetch = HttpFetch(handleFetchResult, 0, 5000)
    reactor.callLater(1, _main, httpfetch)
    httpfetch.run()

if __name__ == "__main__":
    main()
