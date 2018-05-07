# coding:utf8
import os
import ipdb
import torch as t
import torchvision as tv
import tqdm
from model import NetG, NetD
from torch.autograd import Variable
from torchnet.meter import AverageValueMeter


class Config(object):
    data_path = 'data/'  # 数据集存放路径
    num_workers = 4  # 多进程加载数据所用的进程数
    image_size = 96  # 图片尺寸
    batch_size = 256
    max_epoch = 200
    lr1 = 2e-4  # 生成器的学习率
    lr2 = 2e-4  # 判别器的学习率
    beta1 = 0.5  # Adam优化器的beta1参数
    gpu = True  # 是否使用GPU
    nz = 100  # 噪声维度
    ngf = 64  # 生成器feature map数
    ndf = 64  # 判别器feature map数

    save_path = 'imgs/'  # 生成图片保存路径

    vis = True  # 是否使用visdom可视化
    env = 'GAN'  # visdom的env
    plot_every = 20  # 每间隔20 batch，visdom画图一次

    debug_file = '/tmp/debuggan'  # 存在该文件则进入debug模式
    d_every = 1  # 每1个batch训练一次判别器
    g_every = 5  # 每5个batch训练一次生成器
    decay_every = 10  # 没10个epoch保存一次模型
    netd_path = None  # 'checkpoints/netd_.pth' #预训练模型
    netg_path = None  # 'checkpoints/netg_211.pth'

    # 只测试不训练
    gen_img = 'result.png'
    # 从512张生成的图片中保存最好的64张
    gen_num = 64
    gen_search_num = 512
    gen_mean = 0  # 噪声的均值
    gen_std = 1  # 噪声的方差


opt = Config()


def train(**kwargs):
    # 读取参数赋值
    for k_, v_ in kwargs.items():
        setattr(opt, k_, v_)
    # 可视化
    if opt.vis:
        from visualize import Visualizer
        vis = Visualizer(opt.env)
    # 对图片进行操作
    transforms = tv.transforms.Compose([
        tv.transforms.Scale(opt.image_size),
        tv.transforms.CenterCrop(opt.image_size),
        tv.transforms.ToTensor(),
        # 均值方差
        tv.transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
    ])
    # ImageFolder 使用pytorch原生的方法读取图片，并进行操作  封装数据集
    dataset = tv.datasets.ImageFolder(opt.data_path, transform=transforms)
    #数据加载器
    dataloader = t.utils.data.DataLoader(dataset,
                                         batch_size=opt.batch_size,
                                         shuffle=True,
                                         num_workers=opt.num_workers,
                                         drop_last=True
                                         )

    # 定义网络
    netg, netd = NetG(opt), NetD(opt)
    # 把map内容加载到CPU中
    map_location = lambda storage, loc: storage
    # 将预训练的模型都先加载到cpu上
    if opt.netd_path:
        netd.load_state_dict(t.load(opt.netd_path, map_location=map_location))
    if opt.netg_path:
        netg.load_state_dict(t.load(opt.netg_path, map_location=map_location))

    # 定义优化器和损失
    optimizer_g = t.optim.Adam(netg.parameters(), opt.lr1, betas=(opt.beta1, 0.999))
    optimizer_d = t.optim.Adam(netd.parameters(), opt.lr2, betas=(opt.beta1, 0.999))
    # BinaryCrossEntropy二分类交叉熵，常用于二分类问题，当然也可以用于多分类问题
    criterion = t.nn.BCELoss()

    # 真图片label为1，假图片label为0
    # noises为生成网络的输入
    true_labels = Variable(t.ones(opt.batch_size))
    fake_labels = Variable(t.zeros(opt.batch_size))
    # fix_noises是固定值，用来查看每个epoch的变化效果
    fix_noises = Variable(t.randn(opt.batch_size, opt.nz, 1, 1))
    noises = Variable(t.randn(opt.batch_size, opt.nz, 1, 1))
    # AverageValueMeter统计任意添加的变量的方差和均值  可视化的仪表盘
    errord_meter = AverageValueMeter()
    errorg_meter = AverageValueMeter()

    if opt.gpu:
        # 网络转移到GPU
        netd.cuda()
        netg.cuda()
        # 损失函数转移到GPU
        criterion.cuda()
        # 标签转移到GPU
        true_labels, fake_labels = true_labels.cuda(), fake_labels.cuda()
        # 输入噪声转移到GPU
        fix_noises, noises = fix_noises.cuda(), noises.cuda()

    epochs = range(opt.max_epoch)
    for epoch in iter(epochs):

        for ii, (img, _) in tqdm.tqdm(enumerate(dataloader)):
            real_img = Variable(img)
            if opt.gpu:
                real_img = real_img.cuda()
            # 每d_every个batch训练判别器
            if ii % opt.d_every == 0:
                # 训练判别器
                optimizer_d.zero_grad()
                ## 尽可能的把真图片判别为正确
                #一个batchd的真照片判定为1 并反向传播
                output = netd(real_img)
                error_d_real = criterion(output, true_labels)
                #反向传播
                error_d_real.backward()

                ## 尽可能把假图片判别为错误
                # 一个batchd的假照片判定为0 并反向传播
                noises.data.copy_(t.randn(opt.batch_size, opt.nz, 1, 1))
                fake_img = netg(noises).detach()  # 根据噪声生成假图
                output = netd(fake_img)
                error_d_fake = criterion(output, fake_labels)
                error_d_fake.backward()
                #更新可学习参数
                optimizer_d.step()
                # 总误差=识别真实图片误差+假图片误差
                error_d = error_d_fake + error_d_real
                # 将总误差加入仪表板用于可视化显示
                errord_meter.add(error_d.data[0])
            # 每g_every个batch训练生成器
            if ii % opt.g_every == 0:
                # 训练生成器
                optimizer_g.zero_grad()
                noises.data.copy_(t.randn(opt.batch_size, opt.nz, 1, 1))
                # 生成器：噪声生成假图片
                fake_img = netg(noises)
                # 判别器：假图片判别份数
                output = netd(fake_img)
                # 尽量让假图片的份数与真标签接近,让判别器分不出来
                error_g = criterion(output, true_labels)
                error_g.backward()
                # 更新参数
                optimizer_g.step()
                # 将误差加入仪表板用于可视化显示
                errorg_meter.add(error_g.data[0])

            if opt.vis and ii % opt.plot_every == opt.plot_every - 1:
                ## 可视化
                # 进入debug模式
                if os.path.exists(opt.debug_file):
                    ipdb.set_trace()
                # 固定噪声生成假图片
                fix_fake_imgs = netg(fix_noises)
                # 可视化 固定噪声产生的假图片
                vis.images(fix_fake_imgs.data.cpu().numpy()[:64] * 0.5 + 0.5, win='fixfake')
                # 可视化一张真图片。作为对比
                vis.images(real_img.data.cpu().numpy()[:64] * 0.5 + 0.5, win='real')
                # 可视化仪表盘  判别器误差  生成器误差
                vis.plot('errord', errord_meter.value()[0])
                vis.plot('errorg', errorg_meter.value()[0])
        # 每decay_every个epoch之后保存一次模型
        if epoch % opt.decay_every == 0:
            # 保存模型、图片
            tv.utils.save_image(fix_fake_imgs.data[:64], '%s/%s.png' % (opt.save_path, epoch), normalize=True,
                                range=(-1, 1))
            # 保存判别器  生成器
            t.save(netd.state_dict(), 'checkpoints/netd_%s.pth' % epoch)
            t.save(netg.state_dict(), 'checkpoints/netg_%s.pth' % epoch)
            # 清空误差仪表盘
            errord_meter.reset()
            errorg_meter.reset()
            # 重置优化器参数为刚开始的参数
            optimizer_g = t.optim.Adam(netg.parameters(), opt.lr1, betas=(opt.beta1, 0.999))
            optimizer_d = t.optim.Adam(netd.parameters(), opt.lr2, betas=(opt.beta1, 0.999))

# 预测阶段：噪声随机生成动漫头像
def generate(**kwargs):
    """
    随机生成动漫头像，并根据netd的分数选择较好的
    """
    for k_, v_ in kwargs.items():
        setattr(opt, k_, v_)
    # 将网络模型置为预测模式  不保存中间结果，加速
    netg, netd = NetG(opt).eval(), NetD(opt).eval()
    # 初始化gen_search_num张噪声，期望生成gen_search_num张预测图像
    noises = t.randn(opt.gen_search_num, opt.nz, 1, 1).normal_(opt.gen_mean, opt.gen_std)
    noises = Variable(noises, volatile=True)
    # 将模型参数加载到cpu中
    map_location = lambda storage, loc: storage
    netd.load_state_dict(t.load(opt.netd_path, map_location=map_location))
    netg.load_state_dict(t.load(opt.netg_path, map_location=map_location))
    # 模型和输入噪声转到GPU中
    if opt.gpu:
        netd.cuda()
        netg.cuda()
        noises = noises.cuda()

    # 生成图片，并计算图片在判别器的分数
    fake_img = netg(noises)
    scores = netd(fake_img).data

    # 挑选最好的某几张  从512章图片中按分数排序，取前64张  的下标
    indexs = scores.topk(opt.gen_num)[1]
    result = []
    for ii in indexs:
        result.append(fake_img.data[ii])
    # 保存图片
    tv.utils.save_image(t.stack(result), opt.gen_img, normalize=True, range=(-1, 1))


if __name__ == '__main__':
    import fire

    fire.Fire()
